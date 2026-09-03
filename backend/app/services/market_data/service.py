import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.enums import DataSource
from app.models.market import MarketQuote
from app.models.mixins import utcnow
from app.services import bar_store
from app.services.market_data.base import (
    Bar,
    BarFetchError,
    Quote,
    QuoteProvider,
    Timeframe,
    closed_bars,
)
from app.services.market_data.providers.binance_provider import BinanceProvider
from app.services.market_data.providers.yfinance_provider import YFinanceProvider

# yfinance is an unofficial scraper -- polling every symbol at the app's raw
# MARKET_DATA_POLL_INTERVAL_SEC cadence would get an IP rate-limited or
# blocked within hours. Cache each provider's responses briefly instead.
_DEFAULT_TTL_SEC: dict[DataSource, float] = {
    DataSource.YFINANCE: 15.0,
    DataSource.BINANCE: 5.0,
}

# How long a quote may go on being served after the provider stopped
# confirming it. Two things are true at once and this number is where they
# meet: a one-poll hiccup must not open a gap, because no quote means no
# evaluation and a skipped evaluation is a missed alert -- while an indefinite
# failure must not be served as a live price, because every threshold in the
# app is then comparing against a number that can no longer move.
#
# Comfortably more than a handful of failed refreshes at each source's own TTL,
# and far less than the forever it used to be. Binance is shorter because it
# trades around the clock: a two-minute-old crypto price is already a
# different market, whereas an equity quote outside session hours legitimately
# does not change.
_DEFAULT_STALE_LIMIT_SEC: dict[DataSource, float] = {
    DataSource.YFINANCE: 300.0,
    DataSource.BINANCE: 120.0,
}

# Candle history is the far more expensive request -- years of rows per
# symbol -- and a candle that has closed can never change again, so re-asking
# for it on every poll is pure waste aimed straight at the rate limiter.
# Each TTL is a fraction of its own candle, which is the honest bound: it is
# short enough that a newly closed candle is picked up promptly, and long
# enough that nothing is downloaded faster than it can possibly change.
# How long a FAILED bar fetch holds the door shut. Short on purpose: it exists
# to stop a refetch-on-focus page turning one 429 into a hundred, not to decide
# how long a symbol has no history. Fifteen minutes of that was the bug.
FAILED_FETCH_RETRY_SEC = 60.0

_DEFAULT_BAR_TTL_SEC: dict[Timeframe, float] = {
    Timeframe.MINUTE_1: 30.0,
    Timeframe.MINUTE_5: 60.0,
    Timeframe.MINUTE_15: 120.0,
    Timeframe.MINUTE_30: 240.0,
    Timeframe.HOUR_1: 300.0,
    # Never longer than the candle itself lasts: a 4-hour candle cached for
    # five would draw a bar that closed before the one after it.
    Timeframe.HOUR_4: 900.0,
    Timeframe.HOUR_12: 1800.0,
    Timeframe.DAY_1: 900.0,
    Timeframe.WEEK_1: 3600.0,
    Timeframe.MONTH_1: 3600.0,
}

# Enough history for a 200-period indicator to warm up, with room to spare,
# and few enough rows that replaying them through a sandboxed strategy on
# startup costs milliseconds.
DEFAULT_BAR_LIMIT = 300

# 從資料庫撈多深。比 DEFAULT_BAR_LIMIT 深，因為圖表往前拉會問到更深；但有上限，
# 因為這是「抓不到時的底」，不是一份完整的歷史檔案——真的要幾千根的回測，值得為
# 它去問一次上游。
#
# 現在同一個數字也是**存**的上限（bar_store 寫完就修剪到這個深度），所以它只有一
# 份：讀得比存的深會拿到一段空的，存得比讀的深是在免費方案上白佔空間。
MAX_STORED_BARS = bar_store.MAX_STORED_BARS

# 記憶體裡的快取一次最多握著幾根 K 棒（所有代號、所有週期加起來）。
#
# 有上限，是因為 `_bar_cache` 本來沒有任何東西會把它拿掉：TTL 只決定「這一筆還算不算
# 新鮮」，過期的照樣佔著記憶體等下一次同樣的請求把它蓋掉；沒有下一次的就永遠躺著。而
# 深度是使用者決定的——圖表往前拉會問到 MAX_CHART_BARS（3500）根。
#
# 量過：一筆滿的（3500 根 Bar）是 **687 KB**。看過 100 個代號 × 5 個週期就是 344 MB，
# 而免費方案整台 512 MB，策略池自己還佔 60 MB。行程被 OOM 殺掉的意思是每一則提醒都停
# 了，而這只是一份「少問一次上游」的快取。
#
# 上限用**總根數**不是筆數：那才是真正花掉的東西。用筆數的話，同一個數字對盯盤迴圈
# （一筆 300 根）和對深拉的圖表（一筆 3500 根）差了十倍。60,000 根約 12 MB，容得下
# 200 支盯盤策略的序列，或十七張拉到底的圖。
MAX_CACHED_BARS = 60_000


logger = logging.getLogger("app.market_data")


class MarketDataService:
    def __init__(
        self,
        providers: dict[DataSource, QuoteProvider] | None = None,
        ttl_sec: dict[DataSource, float] | None = None,
        bar_ttl_sec: dict[Timeframe, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        stale_limit_sec: dict[DataSource, float] | None = None,
    ) -> None:
        self._providers = providers or {
            DataSource.YFINANCE: YFinanceProvider(),
            DataSource.BINANCE: BinanceProvider(),
        }
        self._ttl_sec = {**_DEFAULT_TTL_SEC, **(ttl_sec or {})}
        self._bar_ttl_sec = {**_DEFAULT_BAR_TTL_SEC, **(bar_ttl_sec or {})}
        self._stale_limit_sec = {**_DEFAULT_STALE_LIMIT_SEC, **(stale_limit_sec or {})}
        self._clock = clock
        # When each symbol was last actually ANSWERED FOR, monotonic. Kept
        # apart from the cache's single per-source timestamp, which records
        # when the bucket was last refreshed as a whole and therefore says
        # nothing about the one symbol inside it that stopped coming back.
        self._answered_at: dict[tuple[DataSource, str], float] = {}
        # (來源, 代號) -> 最後一次「問了但它沒出現在回答裡」是什麼時候（monotonic）。
        #
        # 上游解不出來的代號不會拋例外，它只是不出現在回答裡。所以它永遠留在「還沒拿
        # 到」那一堆，而下面的補抓看到的就是「這個代號還缺著」——於是每一次 TTL 還沒過
        # 的請求，都額外送出一次只為了它的抓取。量過：20 次請求換來 20 次上游呼叫，其
        # 中 16 次是只為了那一個打錯的代號。
        self._unanswered_at: dict[tuple[DataSource, str], float] = {}
        self._cache: dict[DataSource, tuple[float, dict[str, Quote]]] = {}
        # Keyed per symbol and per timeframe, unlike the quote cache's single
        # per-source bucket. That is what keeps one symbol's fetch schedule
        # entirely its own business -- see get_bars(). The stored tuple is
        # (fetched_at, limit_asked_for, bars).
        self._bar_cache: dict[tuple[DataSource, str, Timeframe], tuple[float, int, list[Bar]]] = {}
        # When a bar fetch last FAILED for each key, monotonic. Separate from
        # the cache above because the two answer different questions: that one
        # holds what is known, this one holds when it was last impossible to
        # find out.
        self._bar_failed_at: dict[tuple[DataSource, str, Timeframe], float] = {}
        # One lock per (source, symbol, timeframe), so that two callers wanting
        # the SAME candles at the same time cost one upstream request.
        #
        # The chart makes exactly that pair: GET /bars for the candles and POST
        # /indicators for the lines over them, fired together, and FastAPI runs
        # sync endpoints in a threadpool so they really do overlap. Once the
        # cache is warm this is free; on a cold one both miss and both fetch.
        # Cold is the normal case here -- Render's free tier spins down when
        # idle -- and a rate-limited fetch is precisely the failure that made
        # AAPL read as having no history.
        #
        # PER KEY, never global: a single lock would queue the market loop's
        # whole sweep behind one slow symbol, and 「警告不能停擺」 outranks
        # everything in this product.
        #
        # THE DEPTH IS PART OF THE KEY, and that is not an optimisation. The
        # chart asks for DEFAULT_BAR_LIMIT and a backtest asks for thousands;
        # the providers tail to exactly what was asked, so a 250-bar answer
        # cannot satisfy a 300-bar question. Sharing a lock across depths makes
        # the deeper caller wait for a fetch it then has to repeat -- all of
        # the cost, none of the benefit -- and the caller most likely to be
        # deeper is the market loop.
        self._bar_locks: dict[tuple[DataSource, str, Timeframe, int], threading.Lock] = {}
        self._bar_locks_guard = threading.Lock()

    def get_quotes(self, symbols: list[str], data_source: DataSource) -> dict[str, Quote]:
        if not symbols:
            return {}

        now = self._clock()
        cached_at, cached_quotes = self._cache.get(data_source, (0.0, {}))
        stale = (now - cached_at) > self._ttl_sec.get(data_source, 5.0)
        # 補抓的是「這一輪還缺、而且最近沒有問過而落空」的那些。
        #
        # 少了後半句，一個上游解不出來的代號（打錯的字、台股少打 .TW——service 自己的
        # 註解就列著這兩種）會在每一次 TTL 內的請求上各換來一次專程的抓取，而那條路的
        # 盡頭是 429 或整個 IP 被擋。到那一刻**每一個代號**都抓不到，不只那個打錯的：
        # 警告全面停擺，起因是一個錯字，而使用者不是工程師。
        #
        # 這不是放棄它：完整刷新那條路（TTL 過了就問全部）每次都會再問它一次，所以代
        # 號恢復了——上市了、改名了、或者他把 .TW 補上去了——下一次刷新就拿得到。
        missing = [
            s
            for s in symbols
            if s not in cached_quotes and not self._recently_unanswered(data_source, s, now)
        ]

        if stale or missing:
            provider = self._providers[data_source]
            fetch_list = symbols if stale else missing
            fresh = provider.get_quotes(fetch_list)
            # Stamped here, not in the providers: this is the moment the
            # answer arrived, and it has to survive being served from cache
            # later or a held price is indistinguishable from a live one.
            answered_at = utcnow()
            for quote in fresh.values():
                quote.fetched_at = answered_at
            for symbol in fresh:
                self._answered_at[(data_source, symbol)] = now
            # 誰答了、誰沒答，兩邊都要記：沒答的那個要開始退避，答了的那個要立刻恢復
            # （不然一次上游抖動會讓一個正常的代號被冷落一分鐘）。
            for symbol in fetch_list:
                if symbol in fresh:
                    self._unanswered_at.pop((data_source, symbol), None)
                else:
                    self._unanswered_at[(data_source, symbol)] = now
            cached_quotes = {**cached_quotes, **fresh}

            # Withdraw what the provider has now gone too long without
            # confirming. Without this the merge above preserved a dead
            # symbol's last entry forever -- a full refresh could not dislodge
            # it, because a refresh asks for everything and the merge keeps
            # whatever the answer omitted. Only symbols just asked for are
            # considered; another caller's names are not this fetch's business.
            limit = self._stale_limit_sec.get(data_source, 300.0)
            for symbol in fetch_list:
                if symbol in fresh:
                    continue
                last_ok = self._answered_at.get((data_source, symbol))
                if last_ok is None or (now - last_ok) > limit:
                    cached_quotes.pop(symbol, None)
                    self._answered_at.pop((data_source, symbol), None)
            # Only a full refresh restarts the TTL clock. A backfill must not:
            # providers silently omit symbols they can't resolve (a typo, a
            # delisting, a Taiwan ticker missing its .TW suffix), so that
            # symbol stays permanently "missing" and gets re-fetched every
            # poll. Stamping `now` there re-armed the timer forever, `stale`
            # never came true again, and every OTHER symbol's price froze at
            # its first value -- silently, behind a fresh-looking timestamp,
            # with stop-loss/take-profit comparing against a price that could
            # no longer move.
            self._cache[data_source] = (now if stale else cached_at, cached_quotes)

            # Providers omit what they cannot resolve rather than raising, so
            # a blocked IP, a renamed API field and a mistyped ticker all
            # arrive here as the same quiet gap. Naming the missing symbols is
            # the difference between "the feed broke at 14:05" and the owner
            # noticing days later that no orders ever appeared.
            missing_now = [s for s in fetch_list if s not in fresh]
            if missing_now:
                logger.warning(
                    "%s returned no quote for %s (asked for %s)",
                    data_source.value,
                    ", ".join(sorted(missing_now)),
                    len(fetch_list),
                )

        return {s: cached_quotes[s] for s in symbols if s in cached_quotes}

    def _recently_unanswered(self, data_source: DataSource, symbol: str, now: float) -> bool:
        """剛才問過這個代號、而它沒有出現在回答裡。

        時間窗沿用 FAILED_FETCH_RETRY_SEC，跟 K 棒那邊的失敗退避同一個數字：兩邊擋的
        是同一件事——把一次解不開的請求變成每一次請求都重來一遍。
        """
        asked_at = self._unanswered_at.get((data_source, symbol))
        return asked_at is not None and (now - asked_at) <= FAILED_FETCH_RETRY_SEC

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        data_source: DataSource,
        limit: int = DEFAULT_BAR_LIMIT,
        db: Session | None = None,
    ) -> list[Bar]:
        """Closed candles for one symbol, newest last, served from cache
        between refreshes.

        The cache key includes the symbol, which is the lesson the quote
        cache above paid for: with one shared bucket, a symbol the provider
        could never resolve dragged every other symbol's refresh schedule
        around with it. Here a dead symbol only ever wastes its own slot.

        The depth an entry was fetched at is remembered alongside it, because
        a shallower cached window cannot answer a deeper question. The market
        loop only ever asks for DEFAULT_BAR_LIMIT candles; a backtest over
        several years asks for thousands, and serving it the loop's 300 would
        silently shorten the range the owner asked to test while reporting
        success. A *smaller* limit is still served from cache -- that is the
        common case and the one the rate limiter cares about.
        """
        key = (data_source, symbol, timeframe)
        served = self._cached_bars(key, timeframe, limit)
        if served is not None:
            return served

        # Everyone who missed the cache for this one key now queues here, and
        # all but the first will find the answer already waiting when they get
        # in. Taken AFTER the fast path above so a warm cache never touches a
        # lock at all.
        with self._bar_lock((*key, limit)):
            served = self._cached_bars(key, timeframe, limit)
            if served is not None:
                return served
            self._prime_from_storage(db, key, symbol, timeframe, data_source)
            return self._fetch_bars(key, symbol, timeframe, data_source, limit, db)

    def _prime_from_storage(
        self,
        db: Session | None,
        key: tuple[DataSource, str, Timeframe],
        symbol: str,
        timeframe: Timeframe,
        data_source: DataSource,
    ) -> None:
        """把存下來的 K 棒放進快取，但標成**不新鮮**。

        `cached_at=None` 是這整件事的關鍵。`_cached_bars` 本來就把 None 當成過期，
        所以存下來的東西：

          - 永遠不會走快取捷徑（圖表不會停在重開機那一刻）
          - 永遠不會壓住一次即時重試
          - 但在 `bars = fetched or cached` 和失敗時的 `return cached[-limit:]`
            這兩行既有的韌性裡，變成「抓不到的時候還有東西可以畫」

        也就是說：存下來的不是快取，是抓不到時的底。
        """
        if db is None or key in self._bar_cache:
            return
        try:
            stored = bar_store.load(db, data_source, symbol, timeframe, MAX_STORED_BARS)
        except Exception:  # noqa: BLE001 -- 讀不到存量只是沒有底，不是這次請求的錯
            logger.warning("stored bars unreadable for %s", symbol, exc_info=True)
            return
        if stored:
            self._bar_cache[key] = (None, 0, stored)
            self._evict_until_within_budget(key)

    def cached_bar_count(self) -> int:
        """記憶體裡現在總共握著幾根 K 棒。"""
        return sum(len(bars) for _, _, bars in self._bar_cache.values())

    def _evict_until_within_budget(self, keep: tuple[DataSource, str, Timeframe]) -> None:
        """把快取壓回 MAX_CACHED_BARS 以下，最舊寫入的先丟。

        `keep` 是這一次剛寫進去的那一筆，永遠不丟：丟掉它的話，同一個請求連問兩次就會
        打上游兩次——快取變成負擔而不是幫忙，而且那正好發生在快取滿了、也就是最忙的時
        候。一筆自己就超過預算的也留著（丟掉換不到任何東西：下一次同樣的請求還是會把它
        抓回來，而那一次的答案還是得在記憶體裡存在過）。

        丟的是最舊寫入的，不是最少用到的。盯盤迴圈的那幾筆每個 TTL 就重寫一次，所以它
        們一直是年輕的；被看過一次就再也沒人問的圖表序列會自己老掉。真正要追蹤讀取時間
        的話，每一次快取命中都得改動這個 dict，而那換到的只是更精準地丟掉一筆「再抓回
        來就好」的東西。
        """
        total = self.cached_bar_count()
        if total <= MAX_CACHED_BARS:
            return
        for key in list(self._bar_cache):
            if total <= MAX_CACHED_BARS:
                break
            if key == keep:
                continue
            total -= len(self._bar_cache.pop(key)[2])

    def bars_are_stored(self, symbol: str, timeframe: Timeframe, data_source: DataSource) -> bool:
        """這一批是硬碟上的存量，不是剛剛抓到的。

        判準就是 `_prime_from_storage` 寫進去的那個 `cached_at=None`：一次成功的
        抓取一定會蓋掉它（帶著真的時間戳），所以 None 還在，就代表這一輪沒有任何
        一次抓取成功過。

        圖上要說出來。一張永遠畫得出來的圖會掩蓋一個已經死掉一週的資料源，而那是
        提醒類產品最不能有的東西——畫面看起來正常，而它正在說謊。
        """
        cached_at, _, cached = self._bar_cache.get((data_source, symbol, timeframe), (0.0, 0, []))
        return cached_at is None and bool(cached)

    def bar_fetch_failed(self, symbol: str, timeframe: Timeframe, data_source: DataSource) -> bool:
        """Whether this symbol's last bar fetch could not reach the provider.

        Asked PER KEY rather than read off a 「last call」 attribute on the
        service. Two requests now overlap by design -- the chart fires /bars
        and /indicators together -- and a shared attribute means one symbol's
        outcome can be reported on another symbol's response. That would put
        the permanent sentence (「查不到歷史資料」) on a stock that was merely
        rate limited, which is the exact confusion this flag exists to prevent.
        """
        failed_at = self._bar_failed_at.get((data_source, symbol, timeframe))
        return failed_at is not None and (self._clock() - failed_at) <= FAILED_FETCH_RETRY_SEC

    def _bar_lock(self, key: tuple[DataSource, str, Timeframe, int]) -> threading.Lock:
        with self._bar_locks_guard:
            lock = self._bar_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._bar_locks[key] = lock
            return lock

    def _cached_bars(
        self, key: tuple[DataSource, str, Timeframe], timeframe: Timeframe, limit: int
    ) -> list[Bar] | None:
        """What can be answered without asking the provider, or None.

        None means 「go and fetch」; an empty list is a real answer meaning 「we
        asked and there is nothing」. Keeping those apart is the whole subject
        of test_bars_failure_is_not_an_answer.py.
        """
        now = self._clock()
        cached_at, cached_limit, cached = self._bar_cache.get(key, (None, 0, []))
        fresh = cached_at is not None and (now - cached_at) <= self._bar_ttl_sec[timeframe]
        if fresh and cached_limit >= limit:
            return cached[-limit:]

        # A recent failure holds the door shut, briefly. Without this a page
        # that refetches on focus turns one rate-limited response into a
        # hundred; with a full TTL it turns one into fifteen minutes of a
        # perfectly good symbol reading as delisted.
        failed_at = self._bar_failed_at.get(key)
        if failed_at is not None and (now - failed_at) <= FAILED_FETCH_RETRY_SEC:
            return cached[-limit:]
        return None

    def _fetch_bars(
        self,
        key: tuple[DataSource, str, Timeframe],
        symbol: str,
        timeframe: Timeframe,
        data_source: DataSource,
        limit: int,
        db: Session | None = None,
    ) -> list[Bar]:
        now = self._clock()
        _, _, cached = self._bar_cache.get(key, (None, 0, []))

        try:
            fetched = closed_bars(
                self._providers[data_source].get_bars(symbol, timeframe, limit),
                # Passed through so an intraday candle the session cut short is
                # released when the session ends rather than a flat interval
                # later. Without this argument the clamp exists but never runs.
                data_source=data_source,
            )
        except BarFetchError as exc:
            # A FAILURE IS NOT AN ANSWER, and this line is the whole reason the
            # bug existed. The old code could not tell 「asked, and there is
            # nothing here」 from 「could not ask」, so one 429 on a shared
            # deployment IP was stored as fact and a stock with fifty years of
            # history read as having none for the next fifteen minutes.
            #
            # Still not retried on every request, because the concern the old
            # comment raised is real -- a page that refetches on focus would
            # turn one 429 into a hundred. It waits FAILED_FETCH_RETRY_SEC
            # instead of a full TTL: a transient failure costs a minute, not
            # the fifteen a real answer is worth.
            logger.warning("%s bars failed for %s: %s", data_source.value, symbol, exc)
            # Stamped NOW, not with the `now` read before the fetch. A fetch
            # that fails slowly -- a socket timeout is the normal way this
            # fails -- would otherwise be recorded as having failed a minute
            # ago, so the cooldown is already spent and the page is told
            # 「there is no history」 about the very request that could not be
            # made.
            self._bar_failed_at[key] = self._clock()
            # Whatever history is already held still stands: one failed request
            # must not look like 「this strategy has no history yet」 and
            # silently restart its warm-up.
            return cached[-limit:]

        # An empty ANSWER is a fetch that happened, so it stamps the clock:
        # otherwise a symbol the provider genuinely cannot resolve is
        # re-requested on every single poll, which is exactly how an IP gets
        # blocked. Keeping the previous history rather than replacing it with
        # nothing also stops one thin window from restarting a warm-up.
        bars = fetched or cached
        # Stamped with the limit just asked for even when the answer was empty:
        # it records what was requested, so a repeat of the same request is
        # served from cache rather than hammering a symbol that has nothing.
        self._bar_cache[key] = (now, limit, bars)
        self._evict_until_within_budget(key)
        self._bar_failed_at.pop(key, None)

        # 只寫真的抓到的那些。`bars` 在空答案時會退回舊的快取內容，把它再寫一次
        # 只是把同一批資料重存一遍——而更糟的是，那會讓「這次沒抓到」看起來像一
        # 次成功的抓取。
        if db is not None and fetched:
            try:
                # **先把呼叫端手上還沒送出去的送出去。** 下面那個 `db.rollback()`
                # 回滾的是**整個 session**，不只是這一次存檔。盯盤迴圈把自己的
                # session 傳進來之後，「存不進 K 棒」就有機會連帶把那一輪的訊號、
                # Order、通知紀錄一起丟掉——而警告不能停擺，優先於一根 K 棒存不
                # 存得下來。先 commit 之後，回滾最多只丟得掉這一次存檔自己。
                #
                # 圖表那條路這一句是空的（`/bars` 在這之前唯讀）。
                db.commit()
                bar_store.save(db, data_source, symbol, timeframe, fetched)
                # save() 只 flush，而 `get_db` 在請求結束時 close，close 會把沒 commit
                # 的東西丟掉——少了這一行，資料庫裡永遠一根都沒有，而「重開機
                # 之後還畫得出來」是一個不會有任何東西變紅的空承諾。
                db.commit()
            except Exception:  # noqa: BLE001 -- 存不進去不該讓這次請求失敗
                logger.warning("could not store bars for %s", symbol, exc_info=True)
                db.rollback()
        return bars[-limit:]

    def upsert_quotes(self, db: Session, quotes: dict[str, Quote]) -> None:
        for symbol, quote in quotes.items():
            row = db.get(MarketQuote, symbol)
            if row is None:
                row = MarketQuote(symbol=symbol, data_source=quote.data_source, price=quote.price)
                db.add(row)
            row.data_source = quote.data_source
            row.price = quote.price
            row.prev_close = quote.prev_close
            row.change_pct = quote.change_pct
            row.volume = quote.volume
            row.quote_time = quote.quote_time
            # Never overwrite a known currency with nothing: a provider that
            # stops reporting it for one poll must not erase the label the
            # screen is using.
            if quote.currency:
                row.currency = quote.currency
            # The quote's own arrival time when it has one. Re-stamping
            # 「now」 on every poll is what let a price frozen at 09:00 read as
            # seconds old at 14:00 on the page built to reveal exactly that.
            row.fetched_at = quote.fetched_at or utcnow()
        db.commit()


_default_service = MarketDataService()


def get_market_data_service() -> MarketDataService:
    return _default_service
