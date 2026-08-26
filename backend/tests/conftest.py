from collections.abc import Iterator

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.events import Event, bus


@pytest.fixture(autouse=True)
def _secret_encryption_key(monkeypatch):
    # NotificationChannel.config_encrypted (app/db/types.py::EncryptedJSON)
    # needs a real Fernet key to encrypt/decrypt at all -- every test gets
    # one so this isn't something each test file has to remember.
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _no_outbound_http(monkeypatch):
    """沒有一條測試可以打真的網路。

    行情供應商現在直接打 Yahoo 的 chart 端點（httpx.get），而測試只要沒有把它
    mock 掉就會真的送出去——慢、看網路臉色、而且在 CI 上會變成間歇性紅燈。

    擋的是 module 層級的 `httpx.get`：TestClient 用的是自己的 Client 實例，不受
    影響。想測那條路的測試自己把它換掉（見
    tests/test_bars_come_from_the_chart_endpoint.py）。
    """

    def _refuse(*args, **kwargs):
        raise AssertionError("測試打了真的網路：httpx.get(...)。要測這條路請自己 mock 它。")

    monkeypatch.setattr(httpx, "get", _refuse)


@pytest.fixture
def db_session(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session, monkeypatch):
    # Never start the real background poller in tests -- it would hit the
    # network, run on an unrelated schedule, and race with each test's own
    # scratch DB. Phase 3+ tests exercise the worker's logic directly via
    # market_loop.tick_once(), not through the app's lifespan.
    monkeypatch.setattr("app.config.settings.WORKER_ENABLED", False)
    # Same reasoning: the dispatcher opens its own SessionLocal() outside
    # request scope, so it would silently hit the real (non-test) DB if a
    # test's order/webhook flow triggered it.
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_client(client, monkeypatch):
    """A TestClient with a real registered user and a bearer token pre-attached."""
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post(
        "/api/auth/register",
        json={"email": "fixture-user@example.com", "password": "correct-horse-battery"},
    )
    login_resp = client.post(
        "/api/auth/login",
        data={"username": "fixture-user@example.com", "password": "correct-horse-battery"},
    )
    token = login_resp.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def published_events() -> Iterator[list[Event]]:
    """Every event that reached services.events.bus during the test.

    The bus is what the outside world reacts to -- Telegram/email/push sends,
    the notification log, the WebSocket push -- so it is the only place that
    can answer "was the owner told once, or twice?". Functions that also
    return an event list return it for their caller's convenience; publishing
    is what actually notifies anyone.
    """
    collected: list[Event] = []
    bus.subscribe(collected.append)
    try:
        yield collected
    finally:
        bus.unsubscribe(collected.append)


@pytest.fixture(autouse=True)
def _market_always_open(request, monkeypatch):
    """Pretend every market is trading, unless a test says otherwise.

    The worker refuses to run tick strategies, file stop-loss exits or expire
    pending orders outside session hours (services/market_calendar.py). That
    is the right behaviour and it broke thirty-odd tests that were written
    when the clock did not matter -- they are about risk gates and strategy
    logic, and would otherwise pass or fail depending on what time of day the
    suite happens to run, which is worse than not testing the calendar at all.

    Tests that *are* about market hours opt out with
    `@pytest.mark.real_market_hours` and drive the clock themselves.
    """
    if "real_market_hours" in request.keywords:
        return
    monkeypatch.setattr("app.services.market_calendar.is_open", lambda *a, **k: True)
    monkeypatch.setattr("app.services.market_calendar.any_open", lambda *a, **k: True)


@pytest.fixture
def second_user_headers(auth_client, db_session):
    """A second account on the same deployment, and its bearer token.

    CREATED DIRECTLY IN THE DATABASE, not through /api/auth/register, because
    registration now closes itself the moment a deployment has an owner (see
    tests/test_registration_closes_itself.py). That is the security fix, not an
    obstacle to work around: a second account can still arrive by
    scripts/create_user.py, or already exist on a deployment where
    ALLOW_REGISTRATION was left switched on before the fix landed. The owner's
    data must be safe in that world, so the cross-user tests still need a real
    second identity -- they just cannot mint one through the front door any
    more.
    """
    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    user = User(
        email="second-account@example.com",
        hashed_password=hash_password("a different password entirely"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(subject=str(user.id), token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session", autouse=True)
def _close_strategy_workers_at_the_end():
    """整套跑完把策略子行程關掉。

    #18 之後每一支策略跑在子行程裡，而模組層級的池會被所有測試共用。不收尾的話
    這台機器上會留下幾個佔記憶體的孤兒，而那正是「跑到一半 Failed to start
    threads worker」的來源——那個症狀看起來像程式的 bug，其實是記憶體不夠。
    """
    yield
    from app.services import market_loop

    market_loop.shutdown_strategy_workers()
