"""固定幾個子行程，所有使用者的策略排在上面跑。

介面刻意跟 strategy_runtime.StrategyRegistry 一模一樣（get_or_load / invalidate /
is_cached），所以 market_loop 換過來的時候，呼叫端幾乎不用動。這不是為了省工，是
為了讓這次搬家的 diff 看得出來哪幾行才是真正改變行為的。

＊ 為什麼是固定幾個，不是一支策略一個。

量到的（#18 第 1 步）：搬走 enums 之後，一個子行程啟動約 300 毫秒、佔約 20 MB。
一支一個在數字上是負擔得起的，但**成長沒有上界**——使用者可以寫二十支策略，那就
是 400 MB 和二十個行程，跑在一台免費方案的機器上。固定 N 個把記憶體變成一個常
數：N × 20 MB，跟使用者寫幾支策略無關。

代價是同一個 worker 上的策略會互相影響：一支卡住被殺掉，同一格的其他策略狀態也
跟著沒了。它們下一次呼叫會自動重建（見 _Slot.ensure），所以**不會瞎掉**，只是要
重新暖身（而重新暖身是 PooledStrategy.last_bar_ts 觸發的：實例不在了它就回
None，market_loop 那一輪就拿手上的完整 K 棒重暖一次——沒有它的話，這句承諾沒有任
何程式碼兌現得了）。用 id 取餘數分配，所以那個代價不會集中在某一支身上。

＊ 逾時是怎麼做到的。

父行程等不到回覆就把子行程殺掉。這是這張票相對於舊做法真正買到的東西——
strategy_runtime._guarded 的檔頭誠實地寫著它做不到：

    Python cannot kill a thread, so an abandoned call keeps running
    (burning a core on `while True`) until the process restarts.

行程殺得掉。所以那句話在這條路上不再成立，而
tests/test_the_loop_keeps_alerting_when_strategies_fail.py 裡有一條專門驗它。
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.config import settings
from app.services.market_data.base import Bar, Timeframe
from app.services.strategy_runtime import code_hash
from app.services.strategy_worker import StrategyWorker, WorkerUnavailable

# 幾個子行程。三個是刻意選的：這台目標機器（Render 免費方案）有 0.1 顆 CPU 和
# 512 MB，三個 worker 約 60 MB，而超過三個在單核心上也只是排隊排得更長。
logger = logging.getLogger("app.strategy_pool")

DEFAULT_POOL_SIZE = 3

# 暖機的上界。比一次策略呼叫的期限寬得多，因為這裡等的是 import，不是使用者的程
# 式碼——而在一台 0.1 顆 CPU 的機器上，886 毫秒可以變成好幾秒。
WARMUP_TIMEOUT_SEC = 20.0


def bar_to_wire(bar: Bar) -> dict:
    """一根 K 棒變成送得過管線的東西。

    timestamp 用 isoformat**帶時區**。JSON 沒有 datetime，而少了時區這一段，另一
    端拿到的是一根差了八小時的 K 棒——策略照樣會回一個看起來很正常的訊號，沒有任
    何東西會紅。這是這條管線上最容易安靜壞掉的一格。
    """
    return {
        "symbol": bar.symbol,
        "timeframe": bar.timeframe.value,
        "timestamp": bar.timestamp.isoformat(),
        # 一律 float。Bar 的欄位宣告就是 float，provider 回的也是 float，但這個
        # repo 裡有些地方（測試、以及任何拿 Decimal 建 Bar 的程式碼）會塞 Decimal
        # 進來，而 JSON 送不動 Decimal——不在這裡收斂，那就是一個「平常都好、遇到
        # 某個呼叫端才炸」的邊界。
        #
        # 收斂成 float 不是將就：策略本來就宣告會拿到 float，而帳戶那邊的
        # Decimal 計算用的是父行程手上那份 Bar，沒有經過這條管線。
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": None if bar.volume is None else float(bar.volume),
    }


def bar_from_wire(wire: dict) -> Bar:
    return Bar(
        symbol=wire["symbol"],
        timeframe=Timeframe(wire["timeframe"]),
        timestamp=datetime.fromisoformat(wire["timestamp"]),
        open=wire["open"],
        high=wire["high"],
        low=wire["low"],
        close=wire["close"],
        volume=wire["volume"],
    )


@dataclass
class _Spec:
    """重建的時候需要知道的一切。

    留著原始碼而不是只留一個 id，是因為重建發生在呼叫的當下——那時候手上沒有資料
    庫連線，而去開一個連線只為了讀回一段我們上一秒才讀過的字串，是在盯盤迴圈的熱
    路徑上做一次 I/O。
    """

    source_code: str
    params: dict
    code_hash: str


@dataclass
class _Slot:
    """一個子行程，加上排在它上面的那些策略。"""

    worker: StrategyWorker = field(default_factory=lambda: StrategyWorker(timeout_sec=15.0))
    specs: dict[str, _Spec] = field(default_factory=dict)
    # 現在**這一個**子行程裡真的有的那幾支。跟 specs 分開：specs 是「應該有誰」，
    # 這個是「現在有誰」，而子行程被殺掉重建之後兩者會不一樣——那個差就是要重載
    # 的清單。合成一個欄位的話，重建之後我們會以為它還在，然後拿到一個 KeyError。
    loaded: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ensure(self, key: str) -> StrategyWorker:
        """保證這個 key 現在真的在一個活著的子行程裡。

        **每次呼叫之前都問一遍，而不是載入時問一次。** 同一格上的另一支策略可能在
        這一輪稍早卡住而害整個行程被殺掉；如果不在這裡重建，那支沒事的策略會因為
        鄰居的錯而瞎掉一輪——輪詢週期五秒，那就是五秒沒有人在看它的停損。
        """
        if not self.worker.alive:
            self.worker.start()
            # 那個行程裡的東西全沒了，所以記帳也要跟著歸零——不歸零的話，同一格
            # 上的其他策略會被當成「還在裡面」，下一次呼叫直接拿到 KeyError。
            self.loaded.clear()
            # 只重載被問到的那一支。把整格都重載會把「一支壞策略」變成「每一輪都
            # 重跑一次那支壞策略」，而且它下面的每一支都要陪著等。
            self._reload(key)
        elif key not in self.loaded:
            self._reload(key)
        return self.worker

    def _reload(self, key: str) -> None:
        spec = self.specs.get(key)
        if spec is None:
            raise WorkerUnavailable(f"策略 {key} 沒有登記過，重建不了")
        self.worker.load(key, spec.source_code, spec.params)
        self.loaded.add(key)

    def forget(self, key: str) -> None:
        self.specs.pop(key, None)
        self.loaded.discard(key)


class PooledStrategy:
    """一支跑在子行程裡的策略，從盯盤迴圈看出去的樣子。

    讀得到的欄位跟 strategy_runtime.LoadedStrategy 對齊，因為 market_loop 讀的就是
    那些。唯一住在這邊而不是子行程裡的是 `last_bar_ts`——它是「我們給過它哪一根」
    的記帳，不是策略的狀態，放在子行程裡只會在重建時跟著消失，然後同一根 K 棒被
    餵第二次。
    """

    def __init__(self, pool: StrategyPool, key: str, info: dict) -> None:
        self._pool = pool
        self._key = key
        self.name: str = info["name"]
        self.symbol: str = info["symbol"]
        self.code_hash: str = info["code_hash"]
        self.entry_point: str = info["entry_point"]
        self.timeframe: Timeframe = Timeframe(info["timeframe"])
        self.warmup_bars: int | None = info["warmup_bars"]
        self.declared_params: dict = info["declared_params"]
        self._last_bar_ts: datetime | None = None

    @property
    def last_bar_ts(self) -> datetime | None:
        """我們餵到哪一根了——**只有在被餵的那個實例還活著的時候才算數。**

        這筆記帳放在父行程，所以子行程被殺掉不會清掉它；但那一格上的實例已經沒
        了，下一次呼叫拿到的是 _Slot.ensure 重建出來的一個全新的空實例。兩件事兜
        起來，market_loop 會走「只餵新 K 棒」那條路，於是一支已經在發訊號的 20 日
        均線策略會安靜地退回 HOLD，直到它自己重新累積 20 個交易日——而那期間每一輪
        last_error 都是 None，畫面上完全看不出來它已經瞎了。

        檔頭承諾的是「不會瞎掉，只是要重新暖身」。暖身要有人做，而 K 棒在父行程手
        上（market_loop 每一輪都重抓），所以這裡只要老實回答「沒餵過」，那一輪就會用完
        整的歷史重新暖一次。

        **讀一次就要存起來。** 這個答案會在任兩次讀取之間翻面（release_strategy 跑在請
        求執行緒上、沒拿 slot.lock），所以把它留在一個逐根 K 棒重讀的條件裡，中途
        翻面就是 `bar.timestamp > None` → TypeError——而 TypeError 不是 WorkerUnavailable，
        會被算成使用者的錯。market_loop._run_bar_strategy 把它拉成區域變數就是為了
        這件事，有一條測試守著。
        """
        try:
            live = self._pool._instance_is_live(self._key)
        except Exception:  # noqa: BLE001 -- 問不出來就當它不在：多暖一次身是安全方向，拋例外會被算成策略的錯
            live = False
        if not live:
            return None
        return self._last_bar_ts

    @last_bar_ts.setter
    def last_bar_ts(self, value: datetime | None) -> None:
        self._last_bar_ts = value

    def on_tick(self, price: float) -> str:
        return self._pool._call(self._key, "on_tick", price)

    def on_bar(self, bar: Bar) -> str:
        return self._pool._call(self._key, "on_bar", bar_to_wire(bar))

    def warm_up(self, bars: list[Bar]) -> None:
        self._pool._call(self._key, "warm_up", [bar_to_wire(bar) for bar in bars])


class StrategyPool:
    """固定幾個 worker，策略用 id 取餘數分配上去。"""

    def __init__(self, size: int = DEFAULT_POOL_SIZE) -> None:
        self._slots = [_Slot() for _ in range(size)]
        self._handles: dict[int, PooledStrategy] = {}

    # --- StrategyRegistry 的那三個方法 -------------------------------------

    def get_or_load(
        self, strategy_id: int, source_code: str, params: dict | None = None
    ) -> PooledStrategy:
        key = str(strategy_id)
        slot = self._slot_for(strategy_id)
        current = _Spec(source_code, dict(params or {}), code_hash(source_code))

        cached = self._handles.get(strategy_id)
        known = slot.specs.get(key)
        if cached is not None and known is not None:
            # 參數也是「這支策略是什麼」的一部分。只比原始碼的話，改了參數之後舊
            # 實例會繼續跑：表單顯示 20，策略永遠用 5，而沒有任何地方說得出這件事。
            if known.code_hash == current.code_hash and known.params == current.params:
                return cached
            self._drop(strategy_id)

        slot.specs[key] = current
        with slot.lock:
            if not slot.worker.alive:
                slot.worker.start()
                slot.loaded.clear()
            info = slot.worker.load(key, source_code, current.params)
            slot.loaded.add(key)
        handle = PooledStrategy(self, key, info)
        self._handles[strategy_id] = handle
        return handle

    def invalidate(self, strategy_id: int) -> None:
        self._drop(strategy_id)

    def is_cached(self, strategy_id: int) -> bool:
        return strategy_id in self._handles

    # --- 內部 ---------------------------------------------------------------

    def _slot_for(self, strategy_id: int) -> _Slot:
        return self._slots[strategy_id % len(self._slots)]

    def _instance_is_live(self, key: str) -> bool:
        """那個子行程裡現在真的有這支策略的實例嗎。

        問的條件跟 _Slot.ensure 決定要不要重建的條件是同一個，這是刻意的：ensure
        會重建的每一種情況，都表示上一個實例的累積狀態已經不在了。

        刻意不拿 slot.lock：`_call` 拿的是同一把鎖，在盯盤執行緒的一次屬性讀取裡
        搶它，等於多開一條卡住迴圈的路。沒鎖的最壞情況是多暖一次身，或者拋一個
        AttributeError（`alive` 讀兩次 `self._process`，而 shutdown／ prewarm 會在沒鎖的
        情況下把它設成 None）——後者由呼叫端的 try 接住。
        """
        slot = self._slot_for(int(key))
        return slot.worker.alive and key in slot.loaded

    def _drop(self, strategy_id: int) -> None:
        key = str(strategy_id)
        slot = self._slot_for(strategy_id)
        self._handles.pop(strategy_id, None)
        slot.forget(key)
        if slot.worker.alive:
            # 子行程裡那個實例要真的丟掉。不丟的話它會一直活著佔記憶體，而池子的
            # 「N × 20 MB 是個常數」這個保證就不成立了。
            # 丟不掉就丟不掉：那表示子行程已經不在了，而那正好也達成了目的。
            with contextlib.suppress(Exception):
                slot.worker.drop(key)

    def _call(self, key: str, what: str, payload):
        """真正送出一個請求，並且在送出之前確認對面活著。

        逾時用 STRATEGY_TICK_TIMEOUT_SEC，跟舊做法同一個設定值——換掉的是逾時之後
        會發生什麼事（行程被殺掉，而不是一條執行緒被放生），不是使用者調得動的那
        個數字。
        """
        strategy_id = int(key)
        slot = self._slot_for(strategy_id)
        timeout = settings.STRATEGY_TICK_TIMEOUT_SEC
        with slot.lock:
            worker = slot.ensure(key)
            if what == "on_tick":
                return worker.on_tick(key, float(payload), timeout)
            if what == "on_bar":
                return worker.on_bar(key, payload, timeout)
            worker.warm_up(key, payload, timeout)
            return None

    # --- 生命週期 -----------------------------------------------------------

    def prewarm(self) -> None:
        """把每個 worker 都起起來，並把沙箱先載進去。

        量到的：子行程啟動約 190 毫秒，沙箱 import 約 886 毫秒。加起來一個 worker
        將近 1.1 秒，而三個依序付是 3.4 秒——輪詢週期只有五秒，所以重啟後的第一輪
        會被自己的暖機吃掉大半。使用者對這張票開出的條件是「以不要延遲為主」。

        **並行，而且失敗不算數。** 三個一起暖，1.1 秒就結束；而暖不起來只是「還沒
        暖」，不是故障——真正需要它的時候 ensure() 會再試一次。在這裡拋例外會讓盯
        盤迴圈連第一輪都跑不了，而那是這個產品唯一不能停的東西。
        """

        def _warm(slot: _Slot) -> None:
            try:
                with slot.lock:
                    if not slot.worker.alive:
                        slot.worker.start()
                        slot.loaded.clear()
                    slot.worker.preload(timeout=WARMUP_TIMEOUT_SEC)
            except Exception:  # noqa: BLE001
                logger.warning("策略子行程暖機失敗，第一輪會慢一點", exc_info=True)

        threads = [
            threading.Thread(target=_warm, args=(slot,), daemon=True) for slot in self._slots
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            # 有上界地等：暖機是為了讓第一輪快，為了它把啟動卡住就本末倒置了。
            thread.join(WARMUP_TIMEOUT_SEC)

    def shutdown(self) -> None:
        for slot in self._slots:
            slot.worker.close()
            slot.specs.clear()
            slot.loaded.clear()
        self._handles.clear()


# --- 一次性的工作（驗證、回測）------------------------------------------------
#
# 跟常駐池分開，而且這個分開是必要的：常駐池裡活著的是盯盤策略的**狀態**，而驗證
# 是陌生人按一個按鈕就打得到的東西。排在一起的話，任何人送一支跑不完的策略，就會
# 連帶清掉同一格上真正在盯盤的那幾支的累積狀態——一個不需要權限的操作，弄掉了別人
# 的東西。
#
# 這裡的 worker 反過來是**故意可拋棄的**：它沒有任何要保住的狀態，所以逾時被殺掉
# 之後下一次呼叫重建就好。
_scratch: StrategyWorker | None = None
_scratch_lock = threading.Lock()


def validate(source_code: str, prices: list[float]) -> dict:
    """在子行程裡編譯並試跑一支策略。

    回傳的是資料，不是物件：那個實例留在子行程裡，而把它送過管線就等於把隔離拆掉。
    """
    global _scratch
    with _scratch_lock:
        if _scratch is None or not _scratch.alive:
            _scratch = StrategyWorker(timeout_sec=WARMUP_TIMEOUT_SEC)
            _scratch.start()
        return _scratch.validate(source_code, prices, settings.STRATEGY_VALIDATE_TIMEOUT_SEC)


def describe(source_code: str, params: dict | None = None) -> dict:
    """編譯一次，回報這份原始碼是什麼（名字、代號、進場點、K 棒週期、宣告的參數）。

    「只是編譯一下」不是無害的：編譯會執行類別主體和 `__init__`。所以這也在子行
    程裡，也有期限。
    """
    global _scratch
    with _scratch_lock:
        if _scratch is None or not _scratch.alive:
            _scratch = StrategyWorker(timeout_sec=WARMUP_TIMEOUT_SEC)
            _scratch.start()
        return _scratch.request(
            "compile",
            timeout=settings.STRATEGY_VALIDATE_TIMEOUT_SEC,
            source_code=source_code,
            params=params or {},
        )


def check_params(source_code: str, params: dict) -> None:
    """這段程式碼有沒有宣告這幾個參數。不合就拋 StrategyWorkerError。

    只是編譯一次，但編譯**會執行**類別主體和 __init__——所以這也是在跑使用者的程
    式碼，也必須在子行程裡，也必須有期限。
    """
    describe(source_code, params)


def shutdown_scratch() -> None:
    global _scratch
    with _scratch_lock:
        if _scratch is not None:
            _scratch.close()
            _scratch = None


def replay(source_code: str, params: dict, bars: list[Bar], stored_warmup_bars: int) -> dict:
    """一場回測的訊號，在子行程裡跑出來。

    回的是偵測到的東西（name / symbol / entry_point / timeframe / warmup）加上
    `signals`，外加 `failed_at`：None 是沒事，-1 是暖身就爆了，其他是**被測那一段**
    的索引。錯誤只回文字，訊息由呼叫端重建——例外送不過 JSON 管線，而呼叫端手上才
    有那根 K 棒的時間，那是使用者唯一的線索。
    """
    global _scratch
    with _scratch_lock:
        if _scratch is None or not _scratch.alive:
            _scratch = StrategyWorker(timeout_sec=WARMUP_TIMEOUT_SEC)
            _scratch.start()
        return _scratch.replay(
            source_code,
            params,
            [bar_to_wire(bar) for bar in bars],
            stored_warmup_bars,
            settings.STRATEGY_BACKTEST_TIMEOUT_SEC,
        )


def sweep(
    source_code: str,
    param_sets: list[dict],
    bars: list[Bar],
    stored_warmup_bars: int,
    warmup_override: int | None = None,
) -> dict:
    """一整個參數網格，在子行程裡跑完。

    ＊ 卡住的那一組要被**跳過**，不是讓它毀掉整張表。

    子行程是依序跑的，所以卡在第一組會讓後面每一組都沒機會。「邊跑邊回」只救得了
    已經跑完的那幾組——對這個功能來說那等於沒用：使用者掃參數的時候，網格裡有一整
    區會卡住是**常態**（`window=0` 觸發一個除不完的迴圈就夠了），而他送掃描的原因
    正是他還不知道哪一區是合理的。

    所以逾時之後：殺掉子行程、把當時在跑的那一組標成跑不完、換一個乾淨的子行程接
    著跑剩下的。每一次重建至少消耗掉一組，所以這個迴圈一定會結束。

    ＊ K 棒每一批只序列化一次。

    那才是「一次往返」真正買到的東西，而它在重建之後仍然成立——重送的是剩下那幾組
    要用的同一批 K 棒。
    """
    global _scratch
    wires = [bar_to_wire(bar) for bar in bars]
    deadline = time.monotonic() + settings.STRATEGY_BACKTEST_TIMEOUT_SEC

    remaining = list(param_sets)
    rows: list[dict] = []
    timed_out = False

    while remaining:
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break

        with _scratch_lock:
            if _scratch is None or not _scratch.alive:
                _scratch = StrategyWorker(timeout_sec=WARMUP_TIMEOUT_SEC)
                _scratch.start()
                # **暖機，而且不算進下面那個每組的停滯上限裡。**
                #
                # 這裡是重建路徑：上一批有一組卡住，子行程被殺掉了。新的子行程要
                # 啟動（約 190 毫秒）再載入沙箱（約 886 毫秒）——加起來超過一秒，
                # 而每組的停滯上限可能就是一秒出頭。
                #
                # 不先暖的話，那筆重建的錢會被算在「下一組」頭上，於是它被判定成
                # 卡住、被跳過、又觸發一次重建——**每一組都被前一組的重建成本殺
                # 掉**，而網格越大越糟。在 Render 免費方案（0.1 顆 CPU）上沙箱載
                # 入會慢好幾倍，那條路上一組都跑不完。
                #
                # preload 用它自己的 20 秒預算，跟策略的執行時間無關。
                _scratch.preload(timeout=WARMUP_TIMEOUT_SEC)
            worker = _scratch

        # 每一組的停滯上限：**沒有任何一組可以吃掉整場的預算。**
        #
        # 不設的話，第一組卡死就把總預算用完，後面每一組都變成「沒跑完」——一格
        # 壞掉定義了整張表，而那正是這個設計要避免的事。四分之一是個取捨：夠一
        # 組正常的回測跑完（一場回測本來就只有幾十毫秒的算術），又留得下至少三
        # 次重試的空間。
        #
        # 量的是「多久沒有回報下一組」，不是「這批跑完了沒」。所以一批五十組只
        # 要一直有東西回來，就不會被這個上限打斷。
        per_combo = max(1.0, settings.STRATEGY_BACKTEST_TIMEOUT_SEC / 4)
        batch = worker.sweep(
            source_code,
            remaining,
            wires,
            stored_warmup_bars,
            min(left, per_combo),
            warmup_override=warmup_override,
        )
        rows.extend(batch["rows"])
        done = len(batch["rows"])
        remaining = remaining[done:]

        if not batch["timed_out"]:
            break

        if remaining:
            # 卡住的就是下一個還沒回報的那一組。標掉它，剩下的用新的子行程繼續。
            stuck, remaining = remaining[0], remaining[1:]
            rows.append({"params": stuck, "error": "這一組跑不完，已經中止"})
        timed_out = True

    return {"rows": rows, "timed_out": timed_out and bool(remaining), "unrun": remaining}
