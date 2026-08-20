"""Rebuild the US listings table from the exchanges' own published directory.

    python scripts/refresh_us_listings.py

Writes app/data/us_listings.json, which is what turns 「Apple」 into AAPL -- and,
just as importantly, what lets the app say that 「ZZZZ」 is not a listing.

WHY THIS EXISTS. app/data/us_aliases.json holds 53 tickers with their Chinese
names, which covers the handful an owner in Taiwan is likely to type in Chinese.
Anything else Latin fell through to a SHAPE GUESS: any one-to-five-letter string
was offered as a US ticker with the caveat 「沒有對照表可以核對」. So 「Nvidia」
returned nothing at all, and 「ZZZZ」 was offered as a plausible stock -- the same
「accepted, and can never price」 failure that the Taiwanese half of this module
was built to end.

WHY BUNDLED RATHER THAN FETCHED AT RUNTIME. Same reason as the Taiwanese table,
and the reason is written out in refresh_tw_listings.py: symbol lookup sits on
the front door of adding an alert, and it must not acquire a runtime dependency
on somebody else's website -- least of all an unofficial endpoint that can start
rate-limiting on the day the owner is trying to add a stock. A bundled table
answers instantly, offline, identically for everybody.

THE SOURCE is the NASDAQ Trader symbol directory, which is the industry's own
distribution of what is listed, not a scrape:
  NASDAQ       https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
  everyone else https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
                (NYSE, NYSE American, NYSE Arca, Cboe BZX, IEX)

Both are pipe-delimited with a header row and a 「File Creation Time」 trailer.

Run it when a stock the owner wants is missing. Nothing runs it automatically:
a scheduled job that silently rewrites this file could replace a working table
with an empty one on the day the format changes.
"""

import json
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path

import certifi
import httpx

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "us_listings.json"

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Single-letter codes in otherlisted's Exchange column, spelled out. Anything
# not here keeps its raw letter rather than being dropped: a new venue is not a
# reason to make its listings unfindable.
_EXCHANGES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}

# Boilerplate the directory appends to every name. Stripped so the picker shows
# 「Agilent Technologies, Inc.」 rather than 「Agilent Technologies, Inc. Common
# Stock」 -- with the share class kept, because 「Class B」 is the difference
# between two tickers rather than decoration.
_NAME_SUFFIXES = (
    " - Common Stock",
    " Common Stock",
    " - Common Shares",
    " Common Shares",
    " - Ordinary Shares",
    " Ordinary Shares",
)

# Below this, assume the file changed shape or an error page came back. A US
# market with two thousand listings has not happened; overwriting a working
# table with the remains of an HTML error page would break symbol search in a
# way nobody notices until they try to add a stock.
MIN_EXPECTED = {"nasdaq": 3000, "other": 4000}


def _tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _fetch(url: str) -> list[str]:
    with httpx.Client(verify=_tls_context(), timeout=60) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text.splitlines()


def _clean_name(raw: str) -> str:
    name = raw.strip()
    for suffix in _NAME_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def _to_yahoo(symbol: str) -> str:
    """The directory writes share classes with a dot; yfinance wants a dash.

    BRK.B in the file is BRK-B at the price source. Getting this backwards
    means a symbol that is in the table, offered to the owner, and then never
    prices -- which is the exact failure this table exists to prevent.
    """
    return symbol.replace(".", "-")


def _rows(lines: list[str], symbol_col: int, name_col: int, test_col: int, exchange) -> list[dict]:
    out = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) <= max(symbol_col, name_col, test_col):
            continue
        # Test issues are real rows for symbols that do not trade -- ZAZZT,
        # ZBZZT and friends. Offering one is worse than offering nothing.
        if parts[test_col].strip().upper() == "Y":
            continue
        symbol = parts[symbol_col].strip().upper()
        name = _clean_name(parts[name_col])
        if not symbol or not name:
            continue
        out.append(
            {
                "symbol": _to_yahoo(symbol),
                "name": name,
                "market": exchange(parts) if callable(exchange) else exchange,
            }
        )
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    nasdaq = _rows(_fetch(NASDAQ_URL), symbol_col=0, name_col=1, test_col=3, exchange="NASDAQ")
    other = _rows(
        _fetch(OTHER_URL),
        symbol_col=0,
        name_col=1,
        test_col=6,
        exchange=lambda parts: _EXCHANGES.get(parts[2].strip().upper(), parts[2].strip().upper()),
    )

    for label, rows, floor in (
        ("nasdaq", nasdaq, MIN_EXPECTED["nasdaq"]),
        ("other", other, MIN_EXPECTED["other"]),
    ):
        if len(rows) < floor:
            print(f"只取到 {len(rows)} 筆 {label} 資料，少於預期的 {floor} 筆。")
            print("資料來源可能改了格式或回傳了錯誤頁。沒有覆寫既有的表。")
            return 1

    # A ticker belongs to one venue at a time. If the two files disagree, the
    # first one wins and the collision is reported rather than silently
    # producing two different companies under one symbol.
    seen: dict[str, dict] = {}
    duplicates = []
    for row in sorted(nasdaq + other, key=lambda r: r["symbol"]):
        if row["symbol"] in seen:
            duplicates.append(row["symbol"])
            continue
        seen[row["symbol"]] = row

    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "sources": {"nasdaq": NASDAQ_URL, "other": OTHER_URL},
        "listings": list(seen.values()),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print(
        f"寫入 {OUT.name}：NASDAQ {len(nasdaq)} 檔、其他交易所 {len(other)} 檔，共 {len(seen)} 檔。"
    )
    if duplicates:
        print(f"（略過 {len(duplicates)} 個重複代號：{', '.join(duplicates[:5])}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
