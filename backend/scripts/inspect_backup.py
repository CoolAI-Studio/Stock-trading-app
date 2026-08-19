"""Open a backup file and show what is in it.

Deliberately read-only. Writing one back into a live database is the
"wholesale database replacement" case -- it would overwrite positions and
orders the owner may still be relying on, and an import that half-succeeds is
worse than no import. Restoring is therefore a decision a person makes with
their eyes open, using this to see what they have first.

    python scripts/inspect_backup.py trading-backup-20260819-1430.bak

It asks for the passphrase rather than taking it as an argument: an argument
lands in the shell history and in the process list.
"""

import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import backup  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"找不到檔案：{path}")
        return 1

    passphrase = getpass.getpass("備份密碼：")
    try:
        data = backup.read(path.read_bytes(), passphrase)
    except backup.BackupError as exc:
        print(f"打不開：{exc}")
        return 1

    if data.get("format_version", 0) > backup.FORMAT_VERSION:
        print(
            f"這個備份是較新的格式（v{data['format_version']}），"
            f"目前這份程式只讀得懂 v{backup.FORMAT_VERSION}。"
        )
        return 1

    print(f"備份時間：{data['created_at']}")
    print(f"帳號：{data['account']['email']}")
    for name in (
        "strategies",
        "orders",
        "positions",
        "notification_channels",
        "watchlist",
        "alerts",
    ):
        print(f"  {name}: {len(data.get(name, []))} 筆")

    print("\n策略：")
    for strategy in data.get("strategies", []):
        print(f"  - {strategy['name']}（{strategy['symbol']}）")

    print(
        "\n要看完整內容，加上 --json 導出：\n"
        f"  python {Path(__file__).name} {path} --json > backup.json\n"
        "\n注意：通知管道與券商憑證的設定仍以部署當時的 SECRET_ENCRYPTION_KEY 加密，\n"
        "還原到新環境時那把金鑰要一起帶過去，否則那幾筆解不開。"
    )
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
