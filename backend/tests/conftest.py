import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models
from app.db.base import Base
from app.db.session import get_db
from app.main import app


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
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    try:
        yield TestClient(app)
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
