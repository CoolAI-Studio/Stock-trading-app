"""Rebuild the Taiwanese listings table from the exchanges' own open data.

    python scripts/refresh_tw_listings.py

Writes app/data/tw_listings.json, which is what turns 「台積電」 into 2330.TW.

WHY THIS IS BUNDLED RATHER THAN FETCHED AT RUNTIME. Yahoo's search endpoint --
the obvious candidate, and what the app already uses for prices -- answers HTTP
400 to any query containing Chinese characters. Verified: `2330` returns
2330.TW first, `Apple` returns AAPL first, `台積電` returns a Yahoo error page.
So the one thing the owner is most likely to type is the one thing it cannot
answer, and a table of our own is not an optimisation but the feature.

Bundling it also keeps symbol lookup working when the exchanges' servers are
not. The listings change a few times a month; the alerting must not acquire a
new runtime dependency on a website in order to let somebody add a stock to a
watchlist.

The two sources are the exchanges themselves, so the Chinese names are the
official ones rather than somebody's scrape:
  上市 (TWSE, -> .TW)  https://openapi.twse.com.tw/v1/opendata/t187ap03_L
  上櫃 (TPEx, -> .TWO) https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O

Run it when a stock the owner wants is missing. Nothing runs it automatically:
a scheduled job that silently rewrites this file could replace a working table
with an empty one on the day an exchange changes its JSON.
"""

import json
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path

import certifi
import httpx

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "tw_listings.json"

TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

# The two feeds carry the same information under different key names, which is
# the only reason this mapping exists.
TWSE_KEYS = ("公司代號", "公司名稱", "公司簡稱")
TPEX_KEYS = ("SecuritiesCompanyCode", "CompanyName", "CompanyAbbreviation")

# Below this, assume the feed changed shape or served an error page rather than
# publishing a genuinely tiny market. Overwriting a working table with three
# rows would break symbol search in a way nobody would notice until they tried
# to add a stock.
MIN_EXPECTED = {"TW": 800, "TWO": 600}


def _tls_context() -> ssl.SSLContext:
    """Verify properly, minus one RFC-conformance check the TPEx chain fails.

    Python 3.13 turned VERIFY_X509_STRICT on by default, and TPEx's
    certificate chain omits the Subject Key Identifier extension RFC 5280
    requires, so www.tpex.org.tw fails to connect at all:
    "certificate verify failed: Missing Subject Key Identifier". (curl gets
    through because on Windows it uses Schannel, which does not enforce it.
    openapi.twse.com.tw is fine either way -- this is TPEx alone.)

    Clearing that ONE flag is the whole change. The CA bundle, the chain
    verification and the hostname check all stay on; verify=False would have
    turned the lot off, which for a file that decides which company a symbol
    refers to is not a trade worth making. Delete this the day TPEx fixes
    their chain.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def _fetch(url: str) -> list[dict]:
    with httpx.Client(verify=_tls_context(), timeout=60) as client:
        response = client.get(url, headers={"accept": "application/json"})
        response.raise_for_status()
        return response.json()


def _rows(raw: list[dict], keys: tuple[str, str, str], suffix: str) -> list[dict]:
    code_key, full_key, short_key = keys
    out = []
    for row in raw:
        code = str(row.get(code_key, "")).strip()
        # Warrants, ETNs and similar carry non-numeric or oversized codes and
        # are not things this app can price.
        if not code.isdigit() or not (4 <= len(code) <= 6):
            continue
        out.append(
            {
                "code": code,
                "symbol": f"{code}.{suffix}",
                "short_name": str(row.get(short_key, "")).strip(),
                "full_name": str(row.get(full_key, "")).strip(),
                "board": "上市" if suffix == "TW" else "上櫃",
            }
        )
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    listed = _rows(_fetch(TWSE_URL), TWSE_KEYS, "TW")
    otc = _rows(_fetch(TPEX_URL), TPEX_KEYS, "TWO")

    for name, rows, floor in (
        ("TW", listed, MIN_EXPECTED["TW"]),
        ("TWO", otc, MIN_EXPECTED["TWO"]),
    ):
        if len(rows) < floor:
            print(f"只取到 {len(rows)} 筆 {name} 資料，少於預期的 {floor} 筆。")
            print("資料來源可能改了格式或回傳了錯誤頁。沒有覆寫既有的表。")
            return 1

    merged = sorted(listed + otc, key=lambda row: row["code"])

    # A stock cannot be on both boards, but a code can be reused after a
    # delisting. Keeping both would make search return two different companies
    # under one number with nothing to tell them apart.
    seen: dict[str, dict] = {}
    duplicates = []
    for row in merged:
        if row["symbol"] in seen:
            duplicates.append(row["symbol"])
            continue
        seen[row["symbol"]] = row

    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "sources": {"上市": TWSE_URL, "上櫃": TPEX_URL},
        "listings": list(seen.values()),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print(f"寫入 {OUT.name}：上市 {len(listed)} 檔、上櫃 {len(otc)} 檔，共 {len(seen)} 檔。")
    if duplicates:
        print(f"（略過 {len(duplicates)} 個重複代號：{', '.join(duplicates[:5])}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
