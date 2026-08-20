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
        name=entry["symbol"],
        # The provider's own name, fetched when the table was built. This is
        # the line that lets the owner tell 台積電 from 台積電ADR, or GOOGL from
        # GOOG -- both price, and only the name says which is which.
        detail=entry.get("name", ""),
        market=MARKET_US,
        data_source=DataSource.YFINANCE,
        # Checked against the live feed at build time and reviewed, unlike a
        # bare ticker inferred from its shape.
        verified=True,
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
            )
        ]

    scored: list[tuple[int, dict]] = []
    for row in _listings():
        score = _score(row, text)
        if score is not None:
            scored.append((score, row))
    scored.sort(key=lambda pair: (pair[0], pair[1].get("code", "")))

    matches = [_tw_match(row) for _score_, row in scored[:limit]]

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
            )
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
        listing = next((row for row in _listings() if row.get("code") == text), None)
        suffix = listing["symbol"] if listing else f"{text}.TW"
        return (
            f"台股代號要加上市場後綴，只寫「{text}」會被行情來源當成別的市場的股票。"
            f"請改用 {suffix}。"
        )

    return None


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
        row = next((r for r in _listings() if r.get("code") == text), None)

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
