"""狀態頁上「哪些代號抓不到價格」，只能是你自己的代號。

MEASURED: `system.py` 把 `beat.symbol_gap_sec` 的每一項直接攤開（代號名稱 ＋ 多久
沒有價格），沒有任何 user 過濾。那個 heartbeat 是 module 層級的單例，內容是跨
**全部帳號**的代號聯集——一個人在看什麼股票，是這個 app 裡最私人的東西之一。

同一個回應裡的通知計數是有正確過濾的（`NotificationLog.user_id == user_id`）。
對的寫法和錯的寫法並排在同一支函式裡，而錯的那個只是少寫了一個條件。

（公開的 `/healthz` 已經改成只回數量，那是另一件事。這裡是登入後的那一頁。）

送進 AI 的那份摘要走的是同一個欄位，所以它一起被修好——否則等於換一條路把別人的
持股清單送出去，而且是送到一個外部服務。
"""

from decimal import Decimal

from app.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import worker_health


def _other_account_watching(db_session, symbol: str) -> User:
    other = User(email="someone-else@example.com", hashed_password="x")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    db_session.add(
        Strategy(
            user_id=other.id,
            name="theirs",
            symbol=symbol,
            data_source=DataSource.YFINANCE,
            source_code="class Strategy:\n    pass\n",
            code_hash="hash-theirs",
            default_quantity=Decimal(1),
        )
    )
    db_session.commit()
    return other


def _mine(db_session, auth_client, symbol: str) -> None:
    """The fixture user owns this one."""
    user = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    db_session.add(
        Strategy(
            user_id=user.id,
            name="mine",
            symbol=symbol,
            data_source=DataSource.YFINANCE,
            source_code="class Strategy:\n    pass\n",
            code_hash="hash-mine",
            default_quantity=Decimal(1),
        )
    )
    db_session.commit()


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _stale(monkeypatch, symbols: set[str]) -> None:
    """A heartbeat driven through its real API: these symbols were asked for
    and none of them came back with a price, fifteen minutes ago."""
    clock = _Clock()
    beat = worker_health.WorkerHeartbeat(clock=clock)
    beat.mark_loop()
    beat.mark_poll_success()
    beat.mark_symbols(symbols, set())
    clock.now += 900.0
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    monkeypatch.setattr("app.config.settings.WORKER_ENABLED", True)


def test_another_accounts_symbol_never_appears(auth_client, db_session, monkeypatch):
    _mine(db_session, auth_client, "2330.TW")
    _other_account_watching(db_session, "SECRET.TW")
    _stale(monkeypatch, {"2330.TW", "SECRET.TW"})

    body = auth_client.get("/api/system/status").json()

    listed = {row["symbol"] for row in body["market_data"]["stale_symbols"]}
    assert "SECRET.TW" not in listed


def test_your_own_stale_symbol_still_appears(auth_client, db_session, monkeypatch):
    """把過濾做過頭就等於把這個功能拿掉：一個抓不到價格的代號，是那個人自己要去
    修或刪掉的東西。"""
    _mine(db_session, auth_client, "2330.TW")
    _stale(monkeypatch, {"2330.TW"})

    body = auth_client.get("/api/system/status").json()

    listed = {row["symbol"] for row in body["market_data"]["stale_symbols"]}
    assert "2330.TW" in listed


def test_the_whole_response_never_mentions_it(auth_client, db_session, monkeypatch):
    """不只是那一個欄位：同一個回應裡任何地方出現那個代號，都是同一件事。"""
    _mine(db_session, auth_client, "2330.TW")
    _other_account_watching(db_session, "SECRET.TW")
    _stale(monkeypatch, {"SECRET.TW"})

    response = auth_client.get("/api/system/status")

    assert "SECRET.TW" not in response.text
