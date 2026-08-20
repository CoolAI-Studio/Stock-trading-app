"""Turning what a person types into a symbol this app can actually price.

The owner types 「台積電」 or 「2330」. The app needs 「2330.TW」. Nothing bridged
that gap, and the failure was silent in the worst way: the watchlist accepted
「台積電」, stored it, and then every poll asked Yahoo for a symbol that does not
exist. No price, no row, no error -- and no alert, ever, from a watchlist entry
that looked fine.

「2330」 on its own is worse than useless rather than merely useless: Yahoo
resolves a bare 2330 to a Japanese OTC company, so the owner would have been
watching the wrong stock's price with complete confidence.

WHY A BUNDLED TABLE. Yahoo's search endpoint answers HTTP 400 to any query
containing Chinese characters -- verified against the live service -- so the
one thing the owner is most likely to type is the one thing it cannot resolve.
app/data/tw_listings.json is built from TWSE's and TPEx's own open data by
scripts/refresh_tw_listings.py; it carries the official Chinese names and the
exact .TW/.TWO suffix, and it needs no network at all. Symbol lookup therefore
keeps working when an exchange's website does not, which matters because this
is the front door to setting up an alert.

WHAT THIS DELIBERATELY DOES NOT DO IS GUESS. Every caller gets a list of
candidates to choose from, never a silent substitution. A watchlist quietly
pointing at the wrong company produces confident, wrong alerts -- for an
alerting product that is worse than refusing the input outright.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models.enums import DataSource
from app.services.market_data.base import currency_for

_DATA = Path(__file__).resolve().parent.parent / "data" / "tw_listings.json"
# Chinese and common names for US-listed instruments. There is no registry to
# fetch for these -- Yahoo's search answers HTTP 400 to any query containing
# Chinese, and its quote endpoint answers 401 without a crumb -- so this is a
# curated list. It is not a guess: scripts/refresh_us_aliases.py fetches every
# ticker and writes the PROVIDER'S OWN name into the file, so a wrong entry is
# visible in review and the owner always picks against a name the price feed
# supplied rather than a claim this repo makes.
_US_DATA = Path(__file__).resolve().parent.parent / "data" / "us_aliases.json"

# What a Taiwanese stock code looks like, with or without its board suffix.
# The optional trailing letter is the ETF class marker -- 00631L is 元大台灣50
# 正2, 00632R is the inverse. Without it here, looks_unpriceable() waved a bare
# 00632R through as if it might price, and it became a watchlist row that could
# never fire.
_TW_CODE = re.compile(r"^\d{4,6}[A-Z]?$")
_TW_QUALIFIED = re.compile(r"^(\d{4,6}[A-Z]?)\.(TW|TWO)$", re.IGNORECASE)
# Yahoo's US tickers: letters, sometimes a dot or hyphen class marker
# (BRK.B, BF-B). Deliberately narrow -- anything else is not offered as a US
# guess, because offering a wrong market is how somebody ends up watching the
# wrong instrument.
_US_TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")
# Binance spot pairs. Quote assets this app actually deals in.
_CRYPTO_PAIR = re.compile(r"^[A-Z0-9]{2,10}(USDT|USDC|BTC|ETH|TWD)$")

# Names people use that no registry carries. Three kinds, all of which
# returned nothing before this existed:
#
#   NICKNAMES -- 「護國神山」 is 2330 to everyone in Taiwan, and exchange
#   registries hold legal names, not affectionate ones.
#
#   FORMER NAMES -- 2887 became 台新新光金 after the 2025 merger and 2883 became
#   凱基金. The registry is correct and current, and that is precisely the
#   problem: it only ever holds today's name, so somebody who has called it
#   台新金 for twenty years is told the company does not exist.
#
#   SPOKEN LONG FORMS -- 台灣大哥大 is listed as 台灣大.
#
# Hand-curated because no machine-readable source carries a nickname. Kept
# honest by a test that every target still appears in the registry: an alias
# aimed at a delisted or mistyped code would send somebody to a symbol that
# never prices, which is the failure this module exists to prevent.
TW_ALIASES: dict[str, str] = {
    "護國神山": "2330",
    "台新金": "2887",  # renamed 台新新光金 in 2025
    "開發金": "2883",  # renamed 凱基金
    "中華開發": "2883",
    "台灣大哥大": "3045",
    "中國信託": "2891",
    "台塑石化": "6505",
    "鴻準": "2354",
}

# The exchange spells it BTCUSDT; nobody says that out loud.
CRYPTO_ALIASES: dict[str, str] = {
    "比特幣": "BTCUSDT",
    "BTC": "BTCUSDT",
    "以太幣": "ETHUSDT",
    "乙太幣": "ETHUSDT",
    "ETH": "ETHUSDT",
    "狗狗幣": "DOGEUSDT",
    "索拉納": "SOLUSDT",
}

MARKET_TW = "台股"
MARKET_US = "美股"
MARKET_CRYPTO = "加密貨幣"


@dataclass(frozen=True)
class SymbolMatch:
    """One candidate, in a shape the picker can render without interpreting."""

    symbol: str
    # What the owner will recognise: the Chinese short name for a Taiwanese
    # listing, the ticker itself where that is all we know.
    name: str
    # The line underneath -- board, full company name, or why this is only a
    # guess. Carries the honesty; the name alone cannot.
    detail: str
    market: str
    data_source: DataSource
    # False when this is inferred from the shape of the input rather than
    # looked up in a real listing table. The UI says so, because "we think
    # this might be a US ticker" and "this is 台積電" are different claims.
    verified: bool
    # What a price for this symbol would be denominated in. Carried on the
    # match itself because it is half of what separates 2330.TW from TSM --
    # both answer 「台積電」, both price, and the provider's own name for both is
    # "Taiwan Semiconductor Manufacturing". Only 「台股 · TWD」 versus
    # 「美股 · USD」 tells the owner that 220 means two different things.
    currency: str | None = None


@lru_cache(maxsize=1)
def _listings() -> list[dict]:
    """The bundled table, read once.

    A missing or unreadable file must not take symbol lookup -- or the import
    of anything that touches it -- down with it. Without the table the search
    degrades to shape-based guesses, which is far better than a 500 on the
    page where somebody is trying to add a stock.
    """
    try:
        payload = json.loads(_DATA.read_text(encoding="utf-8"))
        rows = payload.get("listings", [])
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


@lru_cache(maxsize=1)
def _us_aliases() -> list[dict]:
    """The bundled US name table, read once. Absent means the search simply
    loses Chinese names for US stocks -- never an error on the page somebody is
    using to add one."""
    try:
        payload = json.loads(_US_DATA.read_text(encoding="utf-8"))
        rows = payload.get("entries", [])
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _us_match(entry: dict) -> SymbolMatch:
    return SymbolMatch(
        symbol=entry["symbol"],
        # The provider's own name, fetched when the table was built -- the same
        # slot the Taiwanese rows put 台積電 in, so a list of both reads as one
        # list. It used to repeat the ticker here, which rendered as 「TSM TSM」
        # and pushed the only human-readable identifier down into the detail
        # line.
        name=entry.get("name", entry["symbol"]),
        detail="",
        market=MARKET_US,
        data_source=DataSource.YFINANCE,
        # Checked against the live feed at build time and reviewed, unlike a
        # bare ticker inferred from its shape.
        verified=True,
        currency=currency_for(entry["symbol"], DataSource.YFINANCE),
    )


def _adr_match(entry: dict) -> SymbolMatch:
    """The US line of a Taiwanese company, said to be exactly that.

    「美股」 alone does not explain why the same company appears twice, and the
    provider's name is identical for both -- so the row has to say ADR outright
    or the owner has no way to read the difference.
    """
    base = _us_match(entry)
    return SymbolMatch(
        symbol=base.symbol,
        name=base.name,
        detail=f"{entry['adr_of']} 的美股 ADR，與台股掛牌是不同的標的",
        market=base.market,
        data_source=base.data_source,
        verified=base.verified,
        currency=base.currency,
    )


def _us_score(entry: dict, query: str) -> int | None:
    """Lower is better; None is no match. Ranked below the Taiwanese hits on
    purpose -- 台積電 is a Taiwanese company first, and a dropdown read on a
    phone is decided by what is at the top."""
    if query == entry["symbol"]:
        return 0
    aliases = entry.get("aliases", [])
    if query in aliases:
        return 1
    if any(query in alias for alias in aliases):
        return 2
    if query and query.upper() in entry.get("name", "").upper():
        return 3
    return None


def listings_generated_at() -> str | None:
    """When the bundled table was built, so the UI can say how old it is --
    a newly listed company legitimately will not be in it."""
    try:
        return json.loads(_DATA.read_text(encoding="utf-8")).get("generated_at")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def is_taiwanese(symbol: str) -> bool:
    """Whether this names a Taiwanese listing -- 2330.TW, 6488.TWO, 00632R.TW.

    The suffix is what decides it, not the digits: a bare 2330 is not a
    Taiwanese symbol, it is an ambiguous number that several markets answer to,
    which is the whole reason looks_unpriceable() refuses it.
    """
    return bool(_TW_QUALIFIED.match(normalise(symbol)))


def listing_for(symbol: str) -> dict | None:
    """The bundled registry's row for a Taiwanese code, suffix optional.

    None means the code is on neither board. Callers must not read that as
    「invalid」 on its own -- the table is a snapshot and a company listed after
    it was built is legitimately absent, which is why looks_unpriceable() is a
    narrow shape check rather than a whitelist. It IS conclusive for a fake
    feed deciding what a real one would have had a row for.
    """
    text = normalise(symbol)
    qualified = _TW_QUALIFIED.match(text)
    code = qualified.group(1) if qualified else text
    if not _TW_CODE.match(code):
        return None
    return next((row for row in _listings() if row.get("code") == code), None)


def normalise(raw: str) -> str:
    """The stored form of whatever was typed.

    Only the mechanical part -- trimming, and upper-casing Latin so that
    `aapl`, ` AAPL ` and `AAPL` are one symbol rather than three watchlist rows
    that each poll separately. Chinese is left alone; it is never a valid
    symbol and turning it into one here is exactly the guessing this module
    refuses to do.
    """
    text = " ".join(raw.split()).strip()
    return text.upper() if text.isascii() else text


def _tw_match(row: dict, verified: bool = True) -> SymbolMatch:
    return SymbolMatch(
        symbol=row["symbol"],
        name=row.get("short_name") or row["code"],
        detail=f"{row.get('board', '')} · {row.get('full_name', '')}".strip(" ·"),
        market=MARKET_TW,
        data_source=DataSource.YFINANCE,
        verified=verified,
        currency=currency_for(row["symbol"], DataSource.YFINANCE),
    )


def _score(row: dict, query: str) -> int | None:
    """How well a listing answers this query. Lower is better; None is no match.

    The ordering is the whole point of having a score: an exact code or an
    exact short name has to come first, because that is what the owner meant,
    and burying 台積電 under six companies whose full name contains 台積 would
    make the picker useless.
    """
    code = row.get("code", "")
    short = row.get("short_name", "")
    full = row.get("full_name", "")

    if query == code or query == row.get("symbol", ""):
        return 0
    if query == short:
        return 1
    if code.startswith(query):
        return 2
    if short.startswith(query):
        return 3
    if query in short:
        return 4
    if query in full:
        return 5
    return None


def search(query: str, limit: int = 8) -> list[SymbolMatch]:
    """Candidates for what the owner typed, best first. Never a substitution.

    An empty list is a real answer and the caller must show it as one: it means
    "this app cannot price that", which is the message that stops a watchlist
    row being created that will never produce an alert.
    """
    text = normalise(query)
    if not text:
        return []

    qualified = _TW_QUALIFIED.match(text)
    if qualified:
        # Already a valid symbol. Look it up anyway so the picker can show
        # WHICH company it is -- confirming the code is the point, since a
        # typo'd digit is still a perfectly well-formed symbol.
        wanted = f"{qualified.group(1)}.{qualified.group(2).upper()}"
        for row in _listings():
            if row.get("symbol") == wanted:
                return [_tw_match(row)]
        return [
            SymbolMatch(
                symbol=wanted,
                name=wanted,
                detail="格式正確，但不在目前的上市／上櫃清單裡（可能是新上市或已下市）。",
                market=MARKET_TW,
                data_source=DataSource.YFINANCE,
                verified=False,
                currency=currency_for(wanted, DataSource.YFINANCE),
            )
        ]

    # A nickname or a former name resolves to its code, and then the ordinary
    # scoring takes over -- so 「護國神山」 comes back as 台積電, not as a bare
    # number the owner has to recognise.
    aliased = TW_ALIASES.get(text)

    scored: list[tuple[int, dict]] = []
    for row in _listings():
        if aliased is not None and row.get("code") == aliased:
            scored.append((0, row))
            continue
        score = _score(row, text)
        if score is not None:
            scored.append((score, row))
    # Tie-break by NAME LENGTH before code.
    #
    # Sorting ties by code as a string put every 00xxx ETF ahead of every
    # four-digit company, so 「台新」 returned five 台新-branded bond funds and
    # never 台新新光金 -- which was in the table the whole time and simply
    # unreachable. The prefix of an ETF's name is its sponsor, not its
    # identity, and somebody typing two characters of a company name wants the
    # company. The shortest matching name is the closest thing to what they
    # typed. Code stays as the final tie-break so the order is stable.
    scored.sort(
        key=lambda pair: (pair[0], len(pair[1].get("short_name", "")), pair[1].get("code", ""))
    )

    matches = [_tw_match(row) for _score_, row in scored[:limit]]

    # The US line of a Taiwanese company that has one. Offered right after its
    # own Taiwanese row, because otherwise the ambiguity is unreachable rather
    # than resolved: 「台積電」 is a registry hit, so search stopped there and
    # somebody holding TSM never saw it. They would then set 「跌破 220」 meaning
    # US$220 against a NT$2,375 stock, and it would never fire -- once, ever,
    # while the row looked perfectly healthy.
    tw_codes = {row.get("code") for _s, row in scored}
    adr_entries = [
        entry
        for entry in _us_aliases()
        if entry.get("adr_of") in tw_codes and entry["symbol"] not in {m.symbol for m in matches}
    ]
    matches.extend(_adr_match(entry) for entry in adr_entries[: limit - len(matches)])

    # US listings we have a reviewed name for. Ranked after the Taiwanese hits
    # so a Taiwanese company never gets pushed down by an alias.
    us_scored = []
    for entry in _us_aliases():
        score = _us_score(entry, text)
        if score is not None:
            us_scored.append((score, entry))
    us_scored.sort(key=lambda pair: (pair[0], pair[1]["symbol"]))
    known_us = {entry["symbol"] for _s, entry in us_scored}
    matches.extend(_us_match(entry) for _s, entry in us_scored[: limit - len(matches)])

    # A bare code is unambiguous within the markets this app supports, but a
    # US ticker we have no entry for is not something any table can confirm.
    # Offered separately and marked unverified rather than mixed in as if it
    # had been looked up.
    if len(matches) < limit and text not in known_us and _US_TICKER.match(text):
        matches.append(
            SymbolMatch(
                symbol=text,
                name=text,
                detail="美股代號（沒有對照表可以核對，送出後若抓不到報價就是打錯了）",
                market=MARKET_US,
                data_source=DataSource.YFINANCE,
                verified=False,
                currency=currency_for(text, DataSource.YFINANCE),
            )
        )
    pair = CRYPTO_ALIASES.get(text)
    if pair and pair not in {m.symbol for m in matches}:
        matches.insert(
            0,
            SymbolMatch(
                symbol=pair,
                name=text,
                detail="Binance 交易對",
                market=MARKET_CRYPTO,
                data_source=DataSource.BINANCE,
                # From a reviewed list rather than the shape of the input.
                verified=True,
                currency=currency_for(pair, DataSource.BINANCE),
            ),
        )

    if len(matches) < limit and _CRYPTO_PAIR.match(text):
        matches.append(
            SymbolMatch(
                symbol=text,
                name=text,
                detail="Binance 交易對",
                market=MARKET_CRYPTO,
                data_source=DataSource.BINANCE,
                verified=False,
                currency=currency_for(text, DataSource.BINANCE),
            )
        )

    return matches[:limit]


def looks_unpriceable(symbol: str) -> str | None:
    """Why this symbol can never produce a price, or None if it might.

    Deliberately a narrow check on shapes that are KNOWN to be wrong, not a
    whitelist. A whitelist would reject every newly listed company and every
    US ticker the bundled table has never heard of, and being unable to add a
    stock is a worse failure than adding one that turns out not to price.

    The two shapes it does catch are the two the owner actually types:
    Chinese, and a bare Taiwanese code with no board suffix.
    """
    text = normalise(symbol)
    if not text:
        return "請輸入股票代號。"

    if not text.isascii():
        return (
            f"「{text}」是公司名稱，不是代號。請用上面的搜尋選出正確的代號 —— "
            "例如台積電是 2330.TW。"
        )

    if _TW_CODE.match(text):
        # The dangerous one. Yahoo resolves a bare 2330 to an unrelated
        # Japanese OTC company, so this would have priced -- just not the right
        # thing.
        listing = listing_for(text)
        suffix = listing["symbol"] if listing else f"{text}.TW"
        return (
            f"台股代號要加上市場後綴，只寫「{text}」會被行情來源當成別的市場的股票。"
            f"請改用 {suffix}。"
        )

    return None


# yfinance's own crypto tickers: BTC-USD, ETH-USD, SOL-USDT. Real, priceable,
# and traded around the clock -- so they are NOT the mismatch below, and
# market_calendar must not read them as US equities.
_YF_CRYPTO = re.compile(r"^[A-Z0-9]{2,10}-(USD|USDT|USDC|TWD)$")

# A US ticker runs to five letters (see _US_TICKER). Anything longer that ends
# in a stablecoin cannot be one, which is what makes this certain enough to
# refuse. The bound is deliberately loose in the safe direction: 「USDT」 itself,
# and any five-character symbol, is left alone, because refusing a stock the
# owner then cannot add is a worse failure than polling one that turns out not
# to price.
_UNMISTAKABLE_PAIR_MIN_LEN = 6


def market_mismatch(symbol: str, data_source) -> str | None:
    """Why this symbol cannot come from this data source, or None.

    The two halves were never checked against each other, and the mismatch is
    silent in both directions:

      binance + 2330.TW -- Binance does not list Taiwanese equities, so it
      never prices. And market_calendar answers 「cannot tell」 for everything on
      Binance because crypto trades continuously, so the app also polls this
      symbol every five seconds through the night.

      yfinance + BTCUSDT -- yfinance does not serve Binance pair names, so the
      same silence, from the other side.

    REFUSED ONLY WHEN CERTAIN, which is this module's standing rule. Binance
    holding a .TW listing is certain. A seven-character symbol ending in USDT
    being a US ticker is certain. Everything else passes.
    """
    from app.models.enums import DataSource

    text = normalise(symbol)
    if not text:
        # looks_unpriceable owns the empty case, and two validators saying the
        # same thing differently is how error messages start contradicting.
        return None

    if data_source == DataSource.BINANCE:
        if is_taiwanese(text) or _TW_CODE.match(text):
            return f"「{text}」是台股代號，Binance 沒有這個標的。資料來源請改成 yfinance。"
        if "." in text:
            return (
                f"「{text}」帶著市場後綴，不是 Binance 的交易對格式（例如 BTCUSDT）。"
                "資料來源請改成 yfinance。"
            )
        return None

    if (
        _CRYPTO_PAIR.match(text)
        and len(text) >= _UNMISTAKABLE_PAIR_MIN_LEN
        and not _YF_CRYPTO.match(text)
    ):
        return (
            f"「{text}」看起來是 Binance 的交易對，yfinance 抓不到它。"
            "資料來源請改成 Binance，或改用 yfinance 自己的寫法（例如 BTC-USD）。"
        )
    return None


def is_yfinance_crypto(symbol: str) -> bool:
    """BTC-USD and friends: yfinance's own 24-hour instruments.

    Named separately from the mismatch check because market_calendar needs the
    same fact for a different reason -- a bare ticker with no dot was being
    classified as a US equity, so a stop-loss on BTC-USD went unchecked from
    16:00 to 09:30 New York, which is when crypto moves.
    """
    return bool(_YF_CRYPTO.match(normalise(symbol)))


# TradingView's {{exchange}} placeholder, mapped to what this app can price.
# Delayed feeds append _DL or _DLY to the exchange (TradingView's own docs say
# so, and TWSE_DLY:2330 is a real symbol on their site) -- Taiwan's free data is
# 15 minutes delayed, so the suffixed form is what the owner's charts actually
# send. Matching only the bare names would miss every one of them.
_TW_EXCHANGES = {"TWSE": "TW", "TPEX": "TWO"}
_US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "OTC", "NYSEARCA"})
_CRYPTO_EXCHANGES = frozenset({"BINANCE"})


def _clean_exchange(raw: str | None) -> str:
    text = (raw or "").strip().upper()
    for suffix in ("_DLY", "_DL"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def resolve_incoming(raw: str, exchange: str | None = None) -> tuple[str | None, str | None]:
    """Canonicalise a symbol arriving with NOBODY PRESENT to correct it.

    Returns (symbol, note). `symbol` is None when it cannot be resolved --
    which means refuse, not improvise. `note` is set only when the input was
    changed, so a caller can record the adjustment; it is None when nothing
    was touched, because a remark on every single alert would stop being read.

    WHY THIS SUBSTITUTES WHERE search() REFUSES TO. Everywhere a human is
    present the rule is "suggest, never substitute": show candidates and let
    them choose. A TradingView webhook has nobody present, so the only choices
    are to resolve or to drop the alert -- and dropping it is a missed alert,
    which is this product's critical failure.

    Resolving is safe HERE because for a purely numeric code it is a lookup
    with a unique answer rather than a guess: the bundled registry holds every
    Taiwanese code exactly once across both boards (pinned by a test), and
    within the markets this app models a numeric string cannot be a US ticker
    or a Binance pair. That is precisely what Yahoo gets wrong -- it searches
    every market it knows, and answers a bare 2330 with a Japanese company.

    A Chinese company name is NOT resolved, because it has no unique answer:
    「台積」 matches several. That one is refused even with nobody present.
    """
    text = normalise(raw)
    if not text:
        return None, None

    market = _clean_exchange(exchange)

    # A market this app does not model. Refused BY NAME rather than resolved,
    # because a four-digit Japanese or Hong Kong code sits in the same numeric
    # band as a Taiwanese one -- TSE:4502 is Takeda and 4502 is also 健信 --
    # so resolving it from the Taiwanese registry would price the wrong
    # company and record a note claiming the mapping was correct. That is the
    # Yahoo failure this registry exists to prevent, rebuilt from the inside.
    if market and market not in _TW_EXCHANGES and market not in _US_EXCHANGES:
        if market not in _CRYPTO_EXCHANGES:
            return None, f"__unsupported__{market}"

    qualified = _TW_QUALIFIED.match(text)
    if qualified:
        # Already the right shape. Upper-casing a suffix is not an adjustment
        # worth remarking on.
        return f"{qualified.group(1)}.{qualified.group(2).upper()}", None

    if _TW_CODE.match(text):
        row = listing_for(text)

        if market in _TW_EXCHANGES:
            # The chart said which board it was on, so the suffix is known even
            # for something the registry has not heard of yet -- a newly listed
            # ETF, say. No assumption is being made, so no caveat is recorded.
            symbol = row["symbol"] if row else f"{text}.{_TW_EXCHANGES[market]}"
            name = row.get("short_name", "") if row else ""
            return symbol, (
                f"TradingView 送來的是「{text}」（{{{{ticker}}}} 不含交易所），"
                f"依 {market} 對應到 {symbol}{f'（{name}）' if name else ''}。"
            )

        if row is None:
            # Appending .TW anyway would manufacture a symbol that prices as
            # nothing -- or, worse, as something else.
            return None, None

        # No exchange came with it, so this IS an assumption and the note has
        # to read like one rather than as a statement of fact.
        return row["symbol"], (
            f"TradingView 送來的是「{text}」，訊息裡沒有帶 {{{{exchange}}}}，"
            f"假設是台股圖表，依上市櫃清單對應到 {row['symbol']}"
            f"（{row.get('short_name', '')}）。若這其實是日股或港股，"
            "請在 TradingView 的警報訊息裡加上 exchange 欄位。"
        )

    if not text.isascii():
        # No unique answer, and nobody present to pick.
        return None, None

    return text, None
