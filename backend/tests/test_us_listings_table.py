"""Latin queries had no table behind them, only a guess at the shape.

app/data/us_aliases.json holds 53 tickers with their Chinese names, which is
the handful somebody in Taiwan is likely to type in Chinese. Everything else
Latin fell through to `_US_TICKER.match(text)` -- any one-to-five-letter string
was offered as a US listing, labelled 「沒有對照表可以核對」.

So the module had two opposite bugs on the same line:

  「Nvidia」 returned NOTHING. A name is not a ticker shape, so there was
  nothing to guess with and nothing to look it up in.
  「ZZZZ」 returned a plausible-looking stock. It is not listed anywhere, and
  offering it is the same 「accepted, and can never price」 failure that the
  Taiwanese half of this module exists to end.

app/data/us_listings.json is the NASDAQ Trader symbol directory -- the
industry's own distribution of what is listed, not a scrape -- covering NASDAQ,
NYSE, NYSE American, NYSE Arca, Cboe BZX and IEX. Bundled rather than fetched
for the reason written into refresh_tw_listings.py: symbol lookup sits on the
front door of adding an alert and must not acquire a runtime dependency on
somebody else's website.

NOT A WHITELIST, though. A company listed after the table was built is
genuinely absent, and refusing it would mean the owner cannot add a stock that
exists -- the worse failure of the two. An unknown ticker is still offered; it
just says plainly that it is not in the list rather than implying no list
exists.
"""

from app.services import symbol_search
from app.services.symbol_search import search


def _symbols(query: str) -> list[str]:
    return [match.symbol for match in search(query)]


# --- the table itself --------------------------------------------------------


def test_the_table_covers_the_whole_market_rather_than_a_sample():
    """Under a few thousand means the source served an error page and the
    refresh wrote its remains over a working table."""
    assert len(symbol_search._us_listings()) > 9000


def test_share_classes_use_the_form_the_price_source_wants():
    """The directory writes BRK.B; yfinance prices BRK-B. Getting this
    backwards puts a symbol in front of the owner that is in the table, gets
    picked, and then never prices."""
    by_symbol = {row["symbol"] for row in symbol_search._us_listings()}

    assert "BRK-B" in by_symbol
    assert "BRK.B" not in by_symbol


def test_test_issues_are_not_in_it():
    """ZAZZT and friends are real rows in the directory for symbols that do
    not trade."""
    by_symbol = {row["symbol"] for row in symbol_search._us_listings()}

    assert "ZAZZT" not in by_symbol
    assert "ZBZZT" not in by_symbol


# --- finding a company by its name -------------------------------------------


def test_a_company_name_finds_its_ticker():
    assert "NVDA" in _symbols("Nvidia")


def test_a_name_is_matched_regardless_of_case():
    assert "AAPL" in _symbols("apple")


def test_an_exact_ticker_comes_first():
    assert _symbols("AAPL")[0] == "AAPL"


def test_the_result_carries_the_real_company_name():
    match = next(m for m in search("Nvidia") if m.symbol == "NVDA")

    assert "NVIDIA" in match.name.upper()
    assert match.verified is True


def test_the_result_says_which_exchange():
    """「美股」 alone does not distinguish an NYSE listing from an Arca ETF, and
    the market column is where the owner reads what they are buying."""
    match = next(m for m in search("AAPL") if m.symbol == "AAPL")

    assert "NASDAQ" in match.detail or "NASDAQ" in match.market


def test_an_etf_is_findable_by_name():
    assert "SPY" in _symbols("SPDR S&P 500")


# --- what it must NOT do -----------------------------------------------------


def test_an_unlisted_ticker_is_no_longer_presented_as_verified():
    """ZZZZ is not listed anywhere. It may still be offered -- a new listing is
    legitimately absent -- but never as something that was looked up."""
    matches = [m for m in search("ZZZZ") if m.symbol == "ZZZZ"]

    assert matches, "refusing it outright would make a brand-new listing unaddable"
    assert matches[0].verified is False


def test_the_unverified_line_says_it_is_not_in_the_list():
    """It used to say 「沒有對照表可以核對」, which is now simply untrue and reads
    as 「nobody could know」 rather than 「this is not listed」."""
    match = next(m for m in search("ZZZZ") if m.symbol == "ZZZZ")

    assert "沒有對照表" not in match.detail
    assert "清單" in match.detail, match.detail


def test_a_taiwanese_company_still_wins_its_own_name():
    """The US table is far larger than the Taiwanese one. It must not push a
    Taiwanese company off the top of its own search."""
    assert _symbols("台積電")[0] == "2330.TW"


def test_the_taiwanese_search_is_untouched():
    assert _symbols("2330")[0] == "2330.TW"
    assert _symbols("鴻海")[0] == "2317.TW"


def test_a_reviewed_chinese_alias_still_beats_the_raw_table():
    """us_aliases.json is hand-checked; the directory is bulk data. When both
    can answer, the reviewed one is the one that was reviewed."""
    assert _symbols("護國神山")[0] == "2330.TW"


def test_one_company_is_listed_once():
    symbols = _symbols("Apple")

    assert len(symbols) == len(set(symbols))


# --- it has to be fast enough to run on every keystroke ---------------------


def test_a_search_over_thirteen_thousand_rows_is_still_interactive():
    """The picker queries as the owner types. A scan that takes a tenth of a
    second turns the box into something that lags behind the keyboard."""
    import time

    search("app")  # warm the lru_cache; the first read parses the file
    started = time.perf_counter()
    for _ in range(10):
        search("app")
    per_call = (time.perf_counter() - started) / 10

    assert per_call < 0.05, f"{per_call * 1000:.0f}ms per search"


def test_a_company_with_an_adr_is_not_listed_twice():
    """「台積電」 matched TSM twice -- once as 2330's ADR, once on its own Chinese
    alias -- and the picker showed the ticker on two lines, the second with an
    empty detail so it read as a second, nameless instrument. Pre-existing;
    giving every US row a venue is what made it visible."""
    symbols = _symbols("台積電")

    assert len(symbols) == len(set(symbols)), symbols


def test_the_adr_line_is_the_one_that_survives():
    """Of the two, the one that says 「2330 的美股 ADR」 is the one carrying the
    fact the owner needs; 「NYSE」 alone does not explain why the same company
    is on the list twice."""
    tsm = next(m for m in search("台積電") if m.symbol == "TSM")

    assert "ADR" in tsm.detail
