import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.services import market_loop, worker_health


@pytest.fixture(autouse=True)
def _notifications_on(client, monkeypatch):
    """/healthz now reports a muted notifier as a failure -- for this product a
    system that sends no alerts is not healthy.

    Depends on `client` deliberately: conftest mutes notifications INSIDE that
    fixture, so anything that does not order itself after it gets overwritten
    and every test here sees a 503."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)


# A realistic Neon URL, planted where a driver error would carry it, so the
# leak assertions below fail loudly if the endpoint ever echoes an exception.
LEAKY_DSN = "postgresql://trader:hunter2@ep-cold-sun-123.neon.tech/trading"


class _FakeClock:
    """Monotonic clock the test drives by hand, so heartbeat ages can be
    fast-forwarded past the health thresholds without sleeping."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _UnreachableSession:
    """Stands in for a Session whose database went away. Mirrors the real
    failure: SessionLocal() connects lazily, so construction succeeds and the
    first statement is what raises."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception(f"could not connect to {LEAKY_DSN}"))

    def close(self) -> None:
        pass


@pytest.fixture
def worker_clock(client, monkeypatch):
    """Turns the worker back on (the client fixture forces it off) and swaps in
    a heartbeat on a hand-driven clock, so a test can age it at will."""
    monkeypatch.setattr("app.config.settings.WORKER_ENABLED", True)
    clock = _FakeClock()
    monkeypatch.setattr(worker_health, "heartbeat", worker_health.WorkerHeartbeat(clock=clock))
    return clock


def test_healthz_returns_ok_when_every_check_passes(client, worker_clock):
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()
    worker_clock.advance(1.0)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["worker"]["status"] == "ok"
    assert body["checks"]["market_data"]["status"] == "ok"


def test_healthz_503s_when_the_database_is_unreachable(client, worker_clock, monkeypatch):
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()
    monkeypatch.setitem(app.dependency_overrides, get_db, _UnreachableSession)

    response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "fail"
    assert body["checks"]["database"]["status"] == "fail"
    # The endpoint is unauthenticated, and the driver puts the whole DSN in the
    # exception message -- the reason belongs in the log, never in the body.
    assert LEAKY_DSN not in response.text
    assert "hunter2" not in response.text


def test_healthz_503s_when_the_worker_stopped_looping(client, worker_clock):
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()
    worker_clock.advance(settings.HEALTH_MAX_AGE_SEC + 1)

    response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "fail"
    assert body["checks"]["worker"]["status"] == "fail"
    assert body["checks"]["database"]["status"] == "ok"


def test_healthz_503s_when_the_last_successful_poll_is_stale(client, worker_clock):
    worker_health.heartbeat.mark_poll_success()
    worker_clock.advance(settings.HEALTH_MAX_AGE_SEC + 1)
    # The loop itself is still turning -- every tick is just raising.
    worker_health.heartbeat.mark_loop()

    response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["worker"]["status"] == "ok"
    assert body["checks"]["market_data"]["status"] == "fail"
    assert body["checks"]["market_data"]["last_poll_age_sec"] > settings.HEALTH_MAX_AGE_SEC


def test_healthz_stays_ok_when_the_worker_is_intentionally_disabled(client):
    # WORKER_ENABLED is off in the test suite and in some local setups; an
    # idle worker is a configuration, not an outage worth paging for.
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["worker"]["status"] == "disabled"
    assert body["checks"]["market_data"]["status"] == "disabled"


def test_healthz_tolerates_a_cold_start_that_has_not_polled_yet(client, worker_clock):
    worker_clock.advance(settings.HEALTH_STARTUP_GRACE_SEC / 2)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["worker"]["status"] == "starting"
    assert body["checks"]["market_data"]["status"] == "starting"


def test_healthz_503s_when_the_worker_never_polled_after_the_grace_period(client, worker_clock):
    worker_clock.advance(settings.HEALTH_STARTUP_GRACE_SEC + 1)

    response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["worker"]["status"] == "fail"
    assert body["checks"]["market_data"]["status"] == "fail"


async def test_run_forever_records_both_heartbeats_on_a_good_tick(monkeypatch):
    clock = _FakeClock()
    beat = worker_health.WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    monkeypatch.setattr("app.config.settings.MARKET_DATA_POLL_INTERVAL_SEC", 0.01)

    stop_event = asyncio.Event()

    def _good_tick():
        stop_event.set()
        return []

    monkeypatch.setattr(market_loop, "tick_once", _good_tick)

    await market_loop.run_forever(stop_event)

    snapshot = beat.snapshot()
    assert snapshot.last_loop_age_sec is not None
    assert snapshot.last_poll_age_sec is not None


async def test_run_forever_keeps_looping_but_records_no_poll_when_a_tick_raises(monkeypatch):
    clock = _FakeClock()
    beat = worker_health.WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    monkeypatch.setattr("app.config.settings.MARKET_DATA_POLL_INTERVAL_SEC", 0.01)

    stop_event = asyncio.Event()

    def _failing_tick():
        stop_event.set()
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(market_loop, "tick_once", _failing_tick)

    await market_loop.run_forever(stop_event)

    snapshot = beat.snapshot()
    assert snapshot.last_loop_age_sec is not None
    # This is the whole point of two separate marks: the loop is alive, but
    # nothing has actually priced anything, and /healthz must say so.
    assert snapshot.last_poll_age_sec is None


def test_docs_available(client):
    response = client.get("/docs")

    assert response.status_code == 200
