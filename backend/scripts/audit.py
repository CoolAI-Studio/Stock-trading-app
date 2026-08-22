"""稽查員：反覆用不同的方法問同一個問題——這個帳號的東西，會不會被別人看到。

WHY THIS EXISTS, AND WHY IT IS NOT SIMPLY MORE TESTS. The isolation this app
has is real and it is measured: three test files, thirty-eight cases, green.
Every one of them is a HAND-WRITTEN LIST. Add a router tomorrow and nothing
notices that no case mentions it -- the suite stays green by construction,
because a list cannot miss what was never put on it. That is the gap this
closes, and it is why nothing below hard-codes what exists: the operations are
read off the running app, the tables off the model registry. What is written
down is only what must never happen.

Six methods, deliberately independent -- a hole that hides from one shows up
in another:

  一、清冊       每一個操作都要有帳號閘門。沒有的必須出現在下面 PUBLIC_ON_PURPOSE
                 名單上並附理由。新增一個公開端點就會紅燈，除非你說出為什麼。
  二、誘餌（匿名）把每一個操作在沒有登入的情況下打一次，回應裡不准出現任何誘餌。
  三、誘餌（別人）用第二個帳號打遍所有操作，包含把擁有者的資源 id 代進網址。
  四、覆蓋       每一張帶 user_id 的表都要真的被植入過誘餌，否則前兩項掃得再乾淨
                 也只是掃到空氣。植不進去的表會被列出來，不會被默默跳過。
  五、沙箱       策略沙箱的逃逸樣本。這是「別人的資料」之外的另一條路：跑在伺服器上
                 的程式碼可以直接讀走整份部署的秘密，一次全部。
  六、倉庫       工作區裡不准出現金鑰形狀的字串、本機路徑、或私鑰。

CANARIES, NOT PATTERN MATCHING. Every secret is set to a value that exists
nowhere else before the app is imported, and every seeded row carries one too.
The final question is then textual -- did any of those strings come back out --
which is far harder to fool than a rule about what a response 「looks like」.
It is also why the database URL here is a real sqlite path with a nonsense
token in it: 「did the DSN leak」 becomes a substring test.

用法：
    python scripts/audit.py            # 全部
    python scripts/audit.py --fast     # 每次 push 跑的子集（略過倉庫全文掃描）
    python scripts/audit.py --json     # 給機器讀的輸出

離開碼 0 = 沒有發現，1 = 有發現。
"""

import argparse
import json
import os
import re
import secrets

# 只呼叫 git ls-files：固定參數、不經過 shell。
import subprocess  # nosec B404
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Runnable as `python scripts/audit.py` from anywhere, the way the other
# scripts here are. pytest gets this from its own configuration; a script does
# not.
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Operations that legitimately answer without a token. Each needs its reason
# HERE, not in a review comment that scrolls away: this list is the argument
# that the public surface is deliberate. Anything public and not on it is a
# finding, which is the point -- the next public endpoint has to be justified
# to the auditor before it reaches the internet.
PUBLIC_ON_PURPOSE: dict[str, str] = {
    "POST /api/auth/login": "登入本身，沒有它就沒有人進得來",
    "POST /api/auth/register": "只在還沒有任何帳號時有用；有了擁有者之後它自己回 403",
    "GET /healthz": "部署平台的健康檢查和外部看門狗都沒有憑證，公開是刻意的",
    "GET /api/setup/status": "設定沒填完時前端要說得出缺什麼，而那時還沒有帳號可登入",
    "POST /api/setup/generate": "設定頁的「產生金鑰」按鈕，同樣發生在有帳號之前",
    "POST /api/webhooks/tradingview": "TradingView 帶不了 bearer token，它用共享密鑰認證",
    "POST /api/notifications/push/receipt": (
        "瀏覽器的 service worker 拿不到 app 的 JWT，也不需要：RFC 8291 的推播內容是"
        "端到端加密的，只有那個訂閱本身解得開，所以「手上有這個 token」本身就是證明。"
        "而且它一律回 204，不會變成猜 token 的神諭。"
    ),
    "GET /openapi.json": "FastAPI 內建的 schema",
    "GET /docs": "FastAPI 內建的文件頁",
    "GET /redoc": "FastAPI 內建的文件頁",
    "GET /docs/oauth2-redirect": "FastAPI 內建的 OAuth 轉址頁",
}

# The sandbox is the other way out: strategy code runs on the server, so an
# escape reads the deployment's secrets directly instead of one account's rows.
# These are the shapes that worked, or nearly did.
SANDBOX_ESCAPES: list[tuple[str, str]] = [
    (
        "單底線的模組別名",
        'import collections\n        self.x = collections._sys.modules["os"].environ',
    ),
    ("另一個模組的同一條路", 'import random\n        self.x = random._sys.modules["os"].environ'),
    ("任何底線開頭的屬性", "import math\n        self.x = math._whatever"),
    ("藏在格式字串裡的 dunder", '        self.x = "{0.__class__}".format(object())'),
    ("算出來的屬性名", '        self.x = getattr(object(), "__cl" + "ass__")'),
    ("直接找 builtins", "        self.x = __builtins__"),
    ("子類別走訪", "        self.x = object.__subclasses__()"),
    ("從例外物件往上爬", "        self.x = ValueError().__traceback__"),
]

# Key material and machine-specific paths. The repository is public, and each
# of these has been in it at least once.
REPO_FORBIDDEN: list[tuple[str, str]] = [
    (r"C:\\Users\\[A-Za-z]", "維護者的本機路徑（會連帶洩漏電腦使用者名稱）"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私鑰"),
    (r"sk-[A-Za-z0-9]{24,}", "OpenAI 形狀的金鑰"),
    (r"sk-or-v1-[A-Za-z0-9]{24,}", "OpenRouter 金鑰"),
    (r"postgres(?:ql)?://[^\s:/]+:[^\s@]{8,}@", "帶密碼的資料庫連線字串"),
    (r"ghp_[A-Za-z0-9]{30,}", "GitHub token"),
]

# Words that turn a match into a documentation example. A scanner that cries
# about a connection string in a setup guide is a scanner people stop reading,
# and this project has already written down what happens to an alarm that
# fires when nothing is wrong: 「久了我不會相信那個警報器」. Every skip is
# still printed, so nothing goes quiet.
PLACEHOLDER_WORDS = (
    "user:password",
    "username:password",
    "<password>",
    "yourpassword",
    "your_password",
    "changeme",
    "xxxx",
)

# Places where a forbidden-looking string is the POINT: this file's own
# patterns, and the tests that plant fake credentials on purpose.
REPO_SCAN_SKIP = ("backend/scripts/audit.py", "backend/tests/", "__pycache__")

STRATEGY_TEMPLATE = """class Strategy:
    def __init__(self):
        self.name = 'audit'
        self.symbol = '2330.TW'
        self.timeframe = '1d'
{body}

    def on_bar(self, bar) -> str:
        return 'HOLD'
"""


class Finding:
    """One thing that is wrong, in a sentence the owner can act on."""

    def __init__(self, method: str, what: str, detail: str = "") -> None:
        self.method = method
        self.what = what
        self.detail = detail

    def __str__(self) -> str:
        tail = f"\n      {self.detail}" if self.detail else ""
        return f"  [{self.method}] {self.what}{tail}"

    def as_dict(self) -> dict[str, str]:
        return {"method": self.method, "what": self.what, "detail": self.detail}


class Audit:
    def __init__(self, fast: bool = False, write_probe: bool = False) -> None:
        self.fast = fast
        self.write_probe = write_probe
        self.findings: list[Finding] = []
        self.notes: list[str] = []
        self.counts: dict[str, int] = {}
        self.canaries: dict[str, str] = {}

    # -- plumbing ---------------------------------------------------------

    def fail(self, method: str, what: str, detail: str = "") -> None:
        self.findings.append(Finding(method, what, detail))

    def note(self, text: str) -> None:
        self.notes.append(text)

    def mint_canaries(self, tmp: Path) -> None:
        """Values that exist nowhere else, planted before the app is imported."""
        from cryptography.fernet import Fernet

        tag = secrets.token_hex(6)
        dsn_file = tmp / f"CANARY-DSN-{tag}.db"
        self.canaries = {
            "JWT_SECRET": f"CANARY-JWT-{tag}",
            "TV_WEBHOOK_SECRET": f"CANARY-TVSECRET-{tag}",
            "AI_API_KEY": f"CANARY-AIKEY-{tag}",
            "SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "DATABASE_URL": f"CANARY-DSN-{tag}",
            # Two shapes for the owner's own rows: some columns are short
            # (String(20) symbols), and a canary that got truncated to fit
            # would be a canary that can never be found again.
            "OWNER_DATA": f"CANARY-OWNERDATA-{tag}",
            "OWNER_DATA_SHORT": f"CNRY{tag}",
            "OWNER_EMAIL": f"canary-owner-{tag}@example.com",
        }
        os.environ.update(
            {
                "DATABASE_URL": f"sqlite:///{dsn_file.as_posix()}",
                "JWT_SECRET": self.canaries["JWT_SECRET"],
                "TV_WEBHOOK_SECRET": self.canaries["TV_WEBHOOK_SECRET"],
                "SECRET_ENCRYPTION_KEY": self.canaries["SECRET_ENCRYPTION_KEY"],
                "AI_API_KEY": self.canaries["AI_API_KEY"],
                "AI_MODEL": "audit/none",
                "WORKER_ENABLED": "false",
                "NOTIFICATIONS_ENABLED": "false",
                "ALLOW_REGISTRATION": "true",
            }
        )

    def leaked(self, text: str) -> list[str]:
        """Which canaries came back out."""
        return [name for name, value in self.canaries.items() if value and value in text]

    # -- 方法一：清冊 ------------------------------------------------------

    def census(self, app: Any) -> list[tuple[str, str, dict]]:
        spec = app.openapi()
        operations: list[tuple[str, str, dict]] = []
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    operations.append((method.upper(), path, operation))
        self.counts["operations"] = len(operations)

        for method, path, operation in operations:
            if operation.get("security"):
                continue
            if f"{method} {path}" in PUBLIC_ON_PURPOSE:
                continue
            self.fail(
                "一、清冊",
                f"{method} {path} 不需要登入，而它不在「刻意公開」的名單上",
                "如果這是對的，把它加進 scripts/audit.py 的 PUBLIC_ON_PURPOSE 並寫下理由；"
                "如果不是，它現在對全世界開著。",
            )

        # A name on the list that no longer exists is stale permission: it says
        # 「this one is fine」 about something nobody can reach, and the next
        # reader counts it as coverage.
        live = {f"{m} {p}" for m, p, _ in operations}
        for entry in PUBLIC_ON_PURPOSE:
            if entry not in live and not entry.startswith(("GET /docs", "GET /redoc", "GET /open")):
                self.note(f"名單上的 {entry} 已經不存在，可以刪掉（不是漏洞，是過期的許可）")
        return operations

    # -- 方法四：覆蓋（先做，因為二和三要靠它植入的資料）------------------

    def _column_value(self, column: Any) -> Any:
        """A plausible value for one column, carrying a canary when it is text."""
        try:
            python_type = column.type.python_type
        except (NotImplementedError, AttributeError):
            return self.canaries["OWNER_DATA_SHORT"]

        if isinstance(python_type, type) and issubclass(python_type, bool):
            return False
        if isinstance(python_type, type) and issubclass(python_type, int):
            return 1
        if isinstance(python_type, type) and issubclass(python_type, float):
            return 1.0
        if isinstance(python_type, type) and issubclass(python_type, Decimal):
            # Numeric/DECIMAL is not a float subclass, so it used to fall all
            # the way through to the text canary and take the whole table with
            # it -- which is how orders and alerts went unaudited while the
            # report still said 「沒有發現」.
            return Decimal(1)
        if isinstance(python_type, type) and issubclass(python_type, datetime):
            return datetime.now(UTC)
        if isinstance(python_type, type) and issubclass(python_type, dict):
            return {"note": self.canaries["OWNER_DATA"]}
        if isinstance(python_type, type) and issubclass(python_type, list):
            return [self.canaries["OWNER_DATA"]]
        if isinstance(python_type, type) and hasattr(python_type, "__members__"):
            return next(iter(python_type.__members__.values()))

        length = getattr(column.type, "length", None)
        canary = self.canaries["OWNER_DATA"]
        if length is not None and length < len(canary):
            short = self.canaries["OWNER_DATA_SHORT"]
            return short if length >= len(short) else short[:length]
        return canary

    def _try_seed(
        self, session: Any, table: Any, owner_id: int, seeded: dict[str, list[int]]
    ) -> str | None:
        """Insert one canary row. Returns None on success, else why not."""
        values: dict[str, Any] = {}
        for column in table.c:
            if column.name == "user_id":
                values["user_id"] = owner_id
                continue
            if column.primary_key or column.nullable:
                continue
            if column.default is not None or column.server_default is not None:
                continue
            if column.foreign_keys:
                # Something other than the owner. Point it at a row already
                # made if there is one; otherwise this table has to wait for
                # the pass that makes it.
                target = next(iter(column.foreign_keys)).column.table.name
                if not seeded.get(target):
                    return f"需要 {target} 的一列"
                values[column.name] = seeded[target][0]
                continue
            values[column.name] = self._column_value(column)

        try:
            result = session.execute(table.insert().values(**values))
            session.commit()
        except Exception as exc:  # noqa: BLE001 -- 任何失敗都只是「這張表沒掃到」
            session.rollback()
            return f"{type(exc).__name__}: {exc}"
        seeded.setdefault(table.name, []).append(result.inserted_primary_key[0])
        return None

    def seed(self, owner_id: int) -> dict[str, list[int]]:
        """One canary-bearing row in every table that carries a user_id.

        Generic on purpose. A table added next month is seeded by the same code
        that seeded today's, which is the whole difference between an auditor
        and a checklist. What cannot be seeded is REPORTED, never skipped
        quietly -- an unseeded table means the sweeps below passed clean air
        over it, and a report that does not say so is lying by omission.

        Several passes, because tables point at each other: strategy_alerts
        needs a strategy, and the registry hands them over in no useful order.
        The loop stops when a pass makes no progress.
        """
        from app.db.base import Base
        from app.db.session import SessionLocal

        seeded: dict[str, list[int]] = {}
        pending = [
            mapper.local_table
            for mapper in Base.registry.mappers
            if mapper.local_table is not None and "user_id" in mapper.local_table.c
        ]
        blocked: dict[str, str] = {}

        session = SessionLocal()
        try:
            while pending:
                still: list[Any] = []
                blocked = {}
                for table in pending:
                    reason = self._try_seed(session, table, owner_id, seeded)
                    if reason:
                        still.append(table)
                        blocked[table.name] = reason
                if len(still) == len(pending):
                    break
                pending = still
        finally:
            session.close()

        for name, reason in blocked.items():
            self.note(f"{name}：沒辦法自動植入誘餌（{reason}）——這張表沒有被稽查到")

        self.counts["seeded_tables"] = len(seeded)
        self.counts["unseeded_tables"] = len(blocked)
        if not seeded:
            self.fail(
                "四、覆蓋",
                "一張帶 user_id 的表都沒有被植入誘餌，接下來的掃描等於掃空氣",
                "這是稽查員自己壞了，不是這個 app 安全。",
            )
        return seeded

    # -- 方法二、三：誘餌掃描 ---------------------------------------------

    def _fill_path(self, path: str, seeded: dict[str, list[int]]) -> str:
        """Put the OWNER's ids into the URL, which is the whole question."""

        def replace(match: re.Match[str]) -> str:
            name = match.group(1).split(":")[0]
            stem = name.removesuffix("_id")
            for table, ids in seeded.items():
                if stem and (stem in table or table.rstrip("s") == stem):
                    return str(ids[0])
            any_id = next((ids[0] for ids in seeded.values() if ids), 1)
            return str(any_id)

        return re.sub(r"\{([^}]+)\}", replace, path)

    def headers_for(self, user_id: int | None) -> dict[str, str]:
        """A token minted from the user's CURRENT token_version.

        MEASURED: minting once and reusing it made this auditor useless. The
        sweep calls POST /api/auth/logout-everywhere -- it is one of the
        operations, and /api/auth sorts near the front -- which bumps the
        token_version and turns every later request into an anonymous one. The
        report stayed clean because 73 of 82 operations were answered 401 by a
        door that never opened. Reading the version back each time keeps the
        destructive operations IN the sweep, where they belong.
        """
        if user_id is None:
            return {}
        from app.core.security import create_access_token
        from app.db.session import SessionLocal
        from app.models.user import User

        session = SessionLocal()
        try:
            user = session.get(User, user_id)
            token = create_access_token(subject=str(user_id), token_version=user.token_version)
        finally:
            session.close()
        return {"Authorization": f"Bearer {token}"}

    def sweep(
        self,
        client: Any,
        label: str,
        method_name: str,
        as_user: int | None,
        operations: list[tuple[str, str, dict]],
        seeded: dict[str, list[int]],
    ) -> None:
        answered = 0
        histogram: dict[int, int] = {}
        for verb, path, _ in operations:
            url = self._fill_path(path, seeded)
            kwargs: dict[str, Any] = {"headers": self.headers_for(as_user)}
            if verb in ("POST", "PUT", "PATCH"):
                kwargs["json"] = {}
            try:
                response = client.request(verb, url, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- 連線層的錯誤不是稽查結果
                self.note(f"{label}：{verb} {url} 打不出去（{type(exc).__name__}）")
                continue

            body = response.text + json.dumps(dict(response.headers), ensure_ascii=False)
            found = self.leaked(body)
            if found:
                self.fail(
                    method_name,
                    f"{label}打 {verb} {path} 拿到了 {'、'.join(found)}",
                    f"HTTP {response.status_code}，回應開頭：{response.text[:200]}",
                )
            histogram[response.status_code] = histogram.get(response.status_code, 0) + 1
            if response.status_code < 300:
                answered += 1
                if verb != "GET" and "{" in path:
                    self.fail(
                        method_name,
                        f"{label}對擁有者的資源做了 {verb} {path}，"
                        f"而且成功了（HTTP {response.status_code}）",
                        "讀不到還不夠——這是寫得進去。",
                    )
        self.counts[f"answered_{label}"] = answered
        refused = histogram.get(401, 0) + histogram.get(403, 0)
        if as_user is not None and refused > len(operations) // 4:
            self.fail(
                method_name,
                f"{label}有 {refused}/{len(operations)} 個操作被擋在門外，這個掃描等於沒做",
                "帶著登入身分的掃描應該進得去；進不去的話「沒有發現」只代表沒有問到。"
                "先修稽查員，再看結果。",
            )
        self.note(
            f"{label}：狀態碼分佈 "
            + "、".join(f"{code}×{n}" for code, n in sorted(histogram.items()))
        )

    def owner_rows(self, seeded: dict[str, list[int]], owner_id: int, when: str) -> dict[str, int]:
        """How many rows still belong to the owner.

        A different question from anything a response body can answer: a 403 on
        the way out proves nothing about what already happened, and a delete
        that succeeds usually answers with an empty body that no canary search
        can see.

        Only the OWNER's rows, deliberately. Counting whole tables moved the
        number every time the intruder created something of its own, which is
        ordinary behaviour -- and a measure that twitches at ordinary behaviour
        is one people learn to ignore.
        """
        from sqlalchemy import func, select

        from app.db.base import Base
        from app.db.session import SessionLocal

        counts: dict[str, int] = {}
        session = SessionLocal()
        try:
            for mapper in Base.registry.mappers:
                table = mapper.local_table
                if table is None or table.name not in seeded:
                    continue
                counts[table.name] = session.execute(
                    select(func.count()).select_from(table).where(table.c.user_id == owner_id)
                ).scalar_one()
        finally:
            session.close()
        self.counts[f"owner_rows_{when}"] = sum(counts.values())
        return counts

    # -- 方法五：沙箱 ------------------------------------------------------

    def sandbox(self, client: Any, headers: dict[str, str]) -> None:
        for name, body in SANDBOX_ESCAPES:
            source = STRATEGY_TEMPLATE.format(body=body)
            response = client.post(
                "/api/strategies/validate", json={"source_code": source}, headers=headers
            )
            found = self.leaked(response.text)
            if found:
                self.fail(
                    "五、沙箱",
                    f"策略沙箱的「{name}」把 {'、'.join(found)} 帶出來了",
                    f"HTTP {response.status_code}，回應開頭：{response.text[:200]}",
                )
        self.counts["sandbox_escapes_tried"] = len(SANDBOX_ESCAPES)

    # -- 方法六：倉庫 ------------------------------------------------------

    def repo(self) -> None:
        if self.fast:
            self.note("--fast：略過倉庫全文掃描（每週的完整稽查會做）")
            return
        try:
            # 固定參數、不經過 shell、不吃外部輸入。
            listing = subprocess.run(  # nosec B603 B607
                ["git", "ls-files"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            self.note(f"倉庫掃描跳過：git ls-files 失敗（{type(exc).__name__}）")
            return

        patterns = [(re.compile(pattern), why) for pattern, why in REPO_FORBIDDEN]
        scanned = 0
        for relative in listing.stdout.splitlines():
            if any(skip in relative for skip in REPO_SCAN_SKIP):
                continue
            path = REPO_ROOT / relative
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for pattern, why in patterns:
                for match in pattern.finditer(text):
                    line = text[: match.start()].count(chr(10)) + 1
                    found = match.group()
                    if any(word in found.lower() for word in PLACEHOLDER_WORDS):
                        self.note(f"{relative}:{line} 是文件範例（{found[:40]}），不算發現")
                        continue
                    self.fail(
                        "六、倉庫",
                        f"{relative}:{line} 有{why}",
                        f"這個 repo 是公開的。比對到的片段：{found[:60]}",
                    )
        self.counts["files_scanned"] = scanned

    # -- 主流程 ------------------------------------------------------------

    # -- 線上那一份（唯讀）------------------------------------------------

    def live(self, base: str) -> None:
        """The same questions, asked of the deployment that actually exists.

        Everything else here audits the code in this working tree. That is not
        the same thing as what is serving the internet: a deploy can be older,
        a platform can carry an environment variable nobody wrote down, and a
        service can be left switched on from an experiment. This is the only
        method that can see any of that.

        STRICTLY READ-ONLY. GET only, plus ONE deliberately invalid
        registration attempt -- invalid so that it cannot create anything even
        if the door turns out to be open, which is exactly what it is asking.
        Nothing here writes to somebody's live database.
        """
        import urllib.error
        import urllib.request

        base = base.rstrip("/")
        base = base.removesuffix("/healthz")

        def fetch(path: str, data: bytes | None = None) -> tuple[int, str]:
            request = urllib.request.Request(  # noqa: S310 # nosec B310
                f"{base}{path}",
                data=data,
                headers={"Content-Type": "application/json"} if data else {},
                method="POST" if data else "GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 # nosec B310
                    return response.status, response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                return 0, f"{type(exc).__name__}: {exc}"

        status, body = fetch("/healthz")
        if status == 0:
            self.fail("七、線上", f"連不上 {base}/healthz", body)
            return
        self.counts["live_healthz"] = status

        # The public probe must not name what the owner is watching. It once
        # did, and the names are the one part of it that is nobody's business.
        try:
            symbols = json.loads(body).get("checks", {}).get("symbols", {})
        except ValueError:
            symbols = {}
        for key, value in symbols.items():
            if isinstance(value, list) and value:
                self.fail(
                    "七、線上",
                    f"線上的 /healthz 在 checks.symbols.{key} 列出了代號名稱",
                    "這支端點不需要憑證就打得到，而擁有者在看哪些股票不是公開資訊。",
                )

        # Is this deployment claimed? /api/setup/status answers 404 once the
        # setup is finished -- deliberately 404 rather than 403, so a stranger
        # cannot tell a configured deployment from a nonexistent path. That
        # makes it a side-effect-free way to ask the question that matters:
        # an UNCLAIMED deployment is one where the first person to register
        # becomes the owner, and it is on the public internet.
        setup_status, _ = fetch("/api/setup/status")
        if setup_status == 200:
            self.fail(
                "七、線上",
                "這個部署還沒有擁有者帳號，而它在公開網路上",
                "註冊的規則是「第一個註冊的人就是擁有者」。現在去建立你自己的帳號，"
                "越快越好——在那之前，先搶到的人拿走的是整個部署。",
            )
        elif setup_status == 404:
            self.note("線上已經設定完成（/api/setup/status 回 404），所以註冊照規則是關著的")
        else:
            self.note(f"線上的 /api/setup/status 回 {setup_status}，看不出設定完成了沒有")

        if self.write_probe:
            # OPT-IN, and it is not read-only: if the door turns out to be
            # open this CREATES an account on a live deployment. That is also
            # the only way to prove the door rather than infer it, which is
            # why it exists and why it is off by default.
            code, _ = fetch(
                "/api/auth/register",
                data=b'{"email": "audit-probe@example.com", "password": "audit-probe-password-1"}',
            )
            if code in (200, 201):
                self.fail(
                    "七、線上",
                    "線上的註冊是開著的，任何人都可以在你的部署上開帳號",
                    "而且這次探測已經開了一個：audit-probe@example.com。"
                    "去狀態頁確認帳號數，把它刪掉。",
                )
            else:
                self.note(f"寫入探測：註冊回 {code}（不是 200/201，門是關的）")

        status, body = fetch("/openapi.json")
        if status != 200:
            self.note(f"線上的 /openapi.json 回 {status}，沒有辦法比對線上的公開清單")
            return
        try:
            paths = json.loads(body)["paths"]
        except (ValueError, KeyError):
            self.note("線上的 /openapi.json 讀不出 paths")
            return

        checked = 0
        for path, methods in paths.items():
            for method, operation in methods.items():
                verb = method.upper()
                if verb not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    continue
                if operation.get("security"):
                    continue
                checked += 1
                if f"{verb} {path}" in PUBLIC_ON_PURPOSE:
                    continue
                self.fail(
                    "七、線上",
                    f"線上有一個不需要登入的 {verb} {path}，而它不在名單上",
                    "這是「部署出去的那一份」，不是這個工作目錄——兩者可以不一樣，"
                    "而只有這一項看得出差別。",
                )
        self.counts["live_public_operations"] = checked
        self.counts["live_operations"] = sum(len(m) for m in paths.values())
        # Reading the schema at all means the schema is public. Not a leak --
        # no data is in it -- but it hands a stranger the whole map, and a
        # deployment for one person has no audience for it.
        self.note("線上的 /openapi.json 不需要登入就讀得到（這份稽查就是這樣讀的）")

    def run(self) -> int:
        with tempfile.TemporaryDirectory(prefix="audit-", ignore_cleanup_errors=True) as tmpdir:
            self.mint_canaries(Path(tmpdir))

            # Imported only now: everything above had to be in the environment
            # before app.config read it.
            from fastapi.testclient import TestClient

            from app.core.security import hash_password
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            from app.main import SETUP_MODE_REASON, app
            from app.models.user import User

            if SETUP_MODE_REASON is not None:
                self.fail("〇、啟動", f"app 進不了正常模式：{SETUP_MODE_REASON}")
                return self.report()

            Base.metadata.create_all(bind=engine)
            session = SessionLocal()
            try:
                owner = User(
                    email=self.canaries["OWNER_EMAIL"],
                    hashed_password=hash_password("audit-owner-password"),
                )
                intruder = User(
                    email="audit-intruder@example.com",
                    hashed_password=hash_password("audit-intruder-password"),
                )
                session.add_all([owner, intruder])
                session.commit()
                session.refresh(owner)
                session.refresh(intruder)
                owner_id = owner.id
                intruder_id = intruder.id
            finally:
                session.close()

            operations = self.census(app)
            seeded = self.seed(owner_id)
            before = self.owner_rows(seeded, owner_id, "before")

            with TestClient(app) as client:
                self.sweep(client, "沒有登入的人", "二、誘餌（匿名）", None, operations, seeded)
                self.sweep(
                    client, "另一個帳號", "三、誘餌（別的帳號）", intruder_id, operations, seeded
                )
                self.sandbox(client, self.headers_for(owner_id))

            after = self.owner_rows(seeded, owner_id, "after")
            for table, count in before.items():
                if after.get(table, 0) < count:
                    self.fail(
                        "三、誘餌（別的帳號）",
                        f"{table} 少了 {count - after.get(table, 0)} 列",
                        "有東西被別的帳號刪掉了。回應內容看不出來，資料列數看得出來。",
                    )

            self.repo()
            # Windows will not delete a sqlite file the pool still holds open,
            # and an auditor that crashes on the way out reports nothing --
            # which reads exactly like an auditor that found nothing.
            engine.dispose()
        return self.report()

    def report(self) -> int:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        print(f"稽查報告 {stamp}（{'快掃' if self.fast else '完整'}）\n")
        print("查了什麼：")
        for key, value in self.counts.items():
            print(f"  {key}: {value}")
        if self.notes:
            print("\n附註（不是發現，但值得知道）：")
            for note in self.notes:
                print(f"  - {note}")
        if not self.findings:
            print("\n沒有發現。誘餌一個都沒有跑出去。")
            return 0
        print(f"\n發現 {len(self.findings)} 件：\n")
        for finding in self.findings:
            print(finding)
        return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="資料外流稽查")
    parser.add_argument("--fast", action="store_true", help="每次 push 用的子集")
    parser.add_argument("--json", action="store_true", help="機器可讀的輸出")
    parser.add_argument(
        "--live-write-probe",
        action="store_true",
        help="搭配 --url：真的去試註冊。門是開的話這會在線上建立一個帳號（所以預設不做）。",
    )
    parser.add_argument(
        "--url",
        help="改成稽查一個真的在跑的部署（唯讀）。給後端網址或它的 /healthz 都可以。",
    )
    args = parser.parse_args(argv[1:])

    audit = Audit(fast=args.fast, write_probe=args.live_write_probe)
    if args.url:
        # A live deployment cannot be seeded, and must not be: this asks only
        # what a stranger can see from outside.
        audit.live(args.url)
        code = audit.report()
    else:
        code = audit.run()
    if args.json:
        print(
            json.dumps(
                {
                    "findings": [f.as_dict() for f in audit.findings],
                    "notes": audit.notes,
                    "counts": audit.counts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
