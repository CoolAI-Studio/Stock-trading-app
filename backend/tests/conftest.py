import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models
from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _secret_encryption_key(monkeypatch):
    # NotificationChannel.config_encrypted (app/db/types.py::EncryptedJSON)
    # needs a real Fernet key to encrypt/decrypt at all -- every test gets
    # one so this isn't something each test file has to remember.
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())


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
