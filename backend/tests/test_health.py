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
    # 資料庫那一格只有深的那一條會查——淺層不碰資料庫，因為平台的健康檢查一直在
    # 打它，每打一次就把免費方案的運算單元叫醒一次
    # （test_the_health_probe_does_not_keep_the_database_awake）。
    deep = client.get("/healthz", params={"deep": "1"}).json()
    assert deep["checks"]["database"]["status"] == "ok"
    assert body["checks"]["worker"]["status"] == "ok"
    assert body["checks"]["market_data"]["status"] == "ok"


def test_healthz_503s_when_the_database_is_unreachable(client, worker_clock, monkeypatch):
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()
    monkeypatch.setitem(app.dependency_overrides, get_db, _UnreachableSession)

    # **深的那一條。** 沒帶參數的 `/healthz` 是平台的健康檢查在看的，而它失敗 60 秒
    # 就會把行程重開——資料庫不見了重開一萬次也回不來（免費方案運算時數用完的話，那
    # 是半個月的事），所以那一格不算「重開有機會修好」。看門狗問的是另一件事。
    # 見 test_the_probe_render_watches_cannot_restart_him_forever。
    response = client.get("/healthz", params={"deep": "1"})

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
    # 資料庫那一格只有深的那一條會查——淺層不碰資料庫，因為平台的健康檢查一直在
    # 打它，每打一次就把免費方案的運算單元叫醒一次
    # （test_the_health_probe_does_not_keep_the_database_awake）。
    deep = client.get("/healthz", params={"deep": "1"}).json()
    assert deep["checks"]["database"]["status"] == "ok"


def test_healthz_503s_when_the_last_successful_poll_is_stale(client, worker_clock):
    worker_health.heartbeat.mark_poll_success()
    worker_clock.advance(settings.HEALTH_MAX_AGE_SEC + 1)
    # The loop itself is still turning -- every tick is just raising.
    worker_health.heartbeat.mark_loop()

    # **深的那一條。** 沒帶參數的 `/healthz` 是平台的健康檢查在看的，而它失敗 60 秒
    # 就會把行程重開——資料庫不見了重開一萬次也回不來（免費方案運算時數用完的話，那
    # 是半個月的事），所以那一格不算「重開有機會修好」。看門狗問的是另一件事。
    # 見 test_the_probe_render_watches_cannot_restart_him_forever。
    response = client.get("/healthz", params={"deep": "1"})

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


def test_the_docs_page_is_not_served_to_the_public(client):
    """This used to assert 200, from when 「the app is up」 was the question.

    The answer changed deliberately: /docs and /openapi.json hand a complete
    map of every endpoint to anyone who knows the backend's address, and that
    address travels in every request the frontend makes. No user data is in
    the schema, so this was never a leak -- but the owner of a deployment is
    not an engineer and will never open that page, which left its only readers
    being people looking for a way in.

    A developer who wants it sets ENABLE_API_DOCS in their own .env.
    tests/test_the_api_map_is_not_public.py owns the rest of this behaviour."""
    response = client.get("/docs")

    assert response.status_code == 404
