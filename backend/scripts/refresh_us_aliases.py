"""Build the Chinese-name lookup for US-listed instruments.

    python scripts/refresh_us_aliases.py

Writes app/data/us_aliases.json, which is what turns 「輝達」 into NVDA.

WHY THIS EXISTS AT ALL. app/data/tw_listings.json covers Taiwan from the
exchanges' own registries. For US listings there is no equivalent we can fetch:
Yahoo's search endpoint answers HTTP 400 to any query containing Chinese
characters (verified against the live service), and its quote endpoint now
answers 401 without a crumb. So a Taiwanese owner typing 「輝達」 or 「蘋果」 --
which is how they think of these companies -- got nothing at all.

WHY IT IS A HAND-CURATED LIST, AND WHY THAT IS NOT THE SAME AS GUESSING. The
mapping below is asserted by a person, but nothing ships on that assertion
alone: this script fetches every ticker and writes the PROVIDER'S OWN name into
the file next to the Chinese one. An entry whose fetched name does not match
the company the Chinese name refers to is visible in the diff and in code
review, and the search UI shows that fetched name too, so the owner is always
choosing against a name the price feed itself supplied rather than against a
claim this file makes.

A runtime AI lookup is the opposite trade: it covers the long tail, but its
answer is produced fresh each time, reviewed by nobody, and equally confident
when wrong. This file is the part that can be checked once and then trusted.

DELIBERATELY SMALL. It covers what a Taiwanese retail investor actually
searches for, not the whole US market. Anything outside it still works by
ticker (AAPL), which is what people who trade the long tail already type.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yfinance as yf

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "us_aliases.json"

# Chinese/common names -> US ticker. Several aliases per instrument on purpose:
# 「輝達」 and 「英偉達」 are both in daily use, and picking one would leave the
# other person with nothing.
#
# Every entry here is CHECKED by this script against the live feed, and the
# fetched name is written into the output for review. If the fetched name does
# not describe the company the aliases name, the entry is wrong and the diff
# shows it.
# Which Taiwanese code each US line is the ADR of. Searching 「台積電」 has to
# offer BOTH -- somebody holding TSM types the company's name, and if only the
# Taiwanese line comes back they set a US-dollar threshold against a NT$2,375
# stock and it never fires. Kept here rather than in tw_listings.json because
# that file is regenerated wholesale from the exchanges' feeds, which know
# nothing about US listings.
ADR_OF: dict[str, str] = {"TSM": "2330", "UMC": "2303", "ASX": "3711", "CHT": "2412"}

ALIASES: list[tuple[str, list[str]]] = [
    # Semiconductors -- what this owner actually watches
    ("NVDA", ["輝達", "英偉達", "輝達半導體"]),
    ("AMD", ["超微", "超微半導體"]),
    ("INTC", ["英特爾"]),
    ("MU", ["美光"]),
    ("AVGO", ["博通"]),
    ("QCOM", ["高通"]),
    ("TXN", ["德州儀器"]),
    ("AMAT", ["應用材料"]),
    ("ASML", ["艾司摩爾", "阿斯麥"]),
    ("ARM", ["安謀"]),
    # Big tech
    ("AAPL", ["蘋果", "蘋果公司"]),
    ("MSFT", ["微軟"]),
    ("GOOGL", ["谷歌", "字母", "Alphabet"]),
    ("AMZN", ["亞馬遜"]),
    ("META", ["臉書", "Meta"]),
    ("NFLX", ["網飛"]),
    ("TSLA", ["特斯拉"]),
    ("ORCL", ["甲骨文"]),
    ("CRM", ["賽富時"]),
    ("ADBE", ["奧多比"]),
    ("PLTR", ["帕蘭泰爾"]),
    # Taiwanese companies' US listings -- the case most likely to be confused
    # with the Taiwanese line, which is exactly why the market is shown.
    ("TSM", ["台積電ADR", "台積電美股"]),
    ("UMC", ["聯電ADR"]),
    ("ASX", ["日月光ADR"]),
    ("CHT", ["中華電ADR"]),
    # Consumer / industrial / financial names people know
    ("KO", ["可口可樂"]),
    ("MCD", ["麥當勞"]),
    ("SBUX", ["星巴克"]),
    ("NKE", ["耐吉", "Nike"]),
    ("WMT", ["沃爾瑪"]),
    ("COST", ["好市多"]),
    ("DIS", ["迪士尼"]),
    ("BA", ["波音"]),
    ("V", ["Visa", "威士卡"]),
    ("MA", ["萬事達卡"]),
    ("JPM", ["摩根大通"]),
    ("BAC", ["美國銀行"]),
    ("BRK-B", ["波克夏", "巴菲特"]),
    ("XOM", ["埃克森美孚"]),
    ("CVX", ["雪佛龍"]),
    # Healthcare
    ("LLY", ["禮來"]),
    ("JNJ", ["嬌生"]),
    ("PFE", ["輝瑞"]),
    ("UNH", ["聯合健康"]),
    ("NVO", ["諾和諾德"]),
    # ETFs a Taiwanese investor holds
    ("SPY", ["標普500", "S&P500"]),
    ("VOO", ["VOO", "先鋒標普500"]),
    ("QQQ", ["納斯達克100", "那斯達克100"]),
    ("VTI", ["全美股"]),
    ("VT", ["全世界股票"]),
    ("TLT", ["美債20年", "20年期美債"]),
    ("TQQQ", ["三倍做多納斯達克"]),
    ("SOXX", ["費城半導體ETF"]),
]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    entries = []
    failed = []
    for ticker, names in ALIASES:
        try:
            info = yf.Ticker(ticker).get_info()
            name = (info.get("shortName") or info.get("longName") or "").strip()
            price = info.get("regularMarketPrice")
        except Exception as exc:  # noqa: BLE001 -- report and continue
            failed.append(f"{ticker}: {type(exc).__name__} {exc}"[:120])
            continue

        if not name or price is None:
            # An entry that does not price is not one the app can alert on, and
            # shipping it would put a dead symbol in front of the owner.
            failed.append(f"{ticker}: no name or no price ({name!r}, {price!r})")
            continue

        entry = {"symbol": ticker, "name": name, "aliases": names}
        if ticker in ADR_OF:
            entry["adr_of"] = ADR_OF[ticker]
        entries.append(entry)
        print(f"  {ticker:6s} {name}")

    if failed:
        print("\n這些沒有通過驗證，沒有寫進檔案：")
        for line in failed:
            print(f"  - {line}")

    if len(entries) < len(ALIASES) * 0.8:
        print(f"\n只有 {len(entries)}/{len(ALIASES)} 筆通過，太少了，沒有覆寫既有的表。")
        print("多半是行情來源暫時擋住了請求，稍後再試。")
        return 1

    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "note": (
            "手動整理的中文／俗名對照，每一筆的 name 欄位是建表時由行情來源回傳的，"
            "不是人寫的 —— 覆核時請看 name 是否真的是 aliases 指的那家公司。"
        ),
        "entries": sorted(entries, key=lambda row: row["symbol"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    alias_count = sum(len(e["aliases"]) for e in entries)
    print(f"\n寫入 {OUT.name}：{len(entries)} 檔，共 {alias_count} 個別名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
