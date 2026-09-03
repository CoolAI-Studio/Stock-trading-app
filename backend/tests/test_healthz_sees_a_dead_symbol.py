"""One symbol that never prices is one symbol whose alerts have stopped.

/healthz counted CONSECUTIVE EMPTY POLLS -- polls where not a single price came
back anywhere. That catches a blocked IP and a total provider outage, and it
was built for exactly those. It cannot see the case that is far more likely to
happen to a real person: nine of their ten symbols price perfectly and the
tenth never does.

  `if quotes: mark_quotes_fetched()`

One good price clears the run. So the owner's watchlist row for a delisted
stock, a typo that survived the input checks, or a company the feed simply
stopped resolving, sits there while every threshold on it never once evaluates.
No order appears, no alert fires, nothing is logged where anybody looks, and
the health probe -- the one thing that emails when nobody is watching -- reports
green. Forever.

That is 「警告靜悄悄停擺」 in its purest form, and it is the failure this product
says it cannot survive.

WHY THIS IS ALLOWED TO GO RED AND STAY RED. A permanently failing probe is
normally an anti-pattern: it trains the owner to ignore the alarm, and then the
real outage arrives unseen. This one is different because it is ACTIONABLE and
SELF-CLEARING -- the owner fixes the symbol or deletes the row and it goes
green -- and because the alternative is a warning system that is quietly
switched off for that symbol with nothing at all to say so.

The name is in the response, so the watchdog email says which symbol rather
than sending somebody to a dashboard to work it out at whatever hour it landed.
"""

from app.config import settings
from app.services.worker_health import WorkerHeartbeat

# --- the bookkeeping --------------------------------------------------------


def test_a_symbol_that_answers_has_no_gap():
    clock = [0.0]
    beat = WorkerHeartbeat(clock=lambda: clock[0])

    beat.mark_symbols({"2330.TW"}, {"2330.TW"})

    assert beat.snapshot().symbol_gap_sec == {}


def test_a_symbol_that_never_answers_accumulates_a_gap():
    clock = [0.0]
    beat = WorkerHeartbeat(clock=lambda: clock[0])

    beat.mark_symbols({"2330.TW"}, set())
    clock[0] = 600.0
    beat.mark_symbols({"2330.TW"}, set())

    assert beat.snapshot().symbol_gap_sec == {"2330.TW": 600.0}


def test_the_gap_is_measured_from_the_last_good_price_not_from_startup():
    """A symbol that worked all morning and died at noon has been dead since
    noon, not since the process booted."""
    clock = [0.0]
    beat = WorkerHeartbeat(clock=lambda: clock[0])
    beat.mark_symbols({"2330.TW"}, {"2330.TW"})

    clock[0] = 1000.0
    beat.mark_symbols({"2330.TW"}, set())
    clock[0] = 1300.0

    assert beat.snapshot().symbol_gap_sec == {"2330.TW": 300.0}


def test_one_good_price_clears_the_gap():
    clock = [0.0]
    beat = WorkerHeartbeat(clock=lambda: clock[0])
    beat.mark_symbols({"2330.TW"}, set())
    clock[0] = 600.0

    beat.mark_symbols({"2330.TW"}, {"2330.TW"})

    assert beat.snapshot().symbol_gap_sec == {}


def test_a_symbol_nobody_watches_any_more_is_forgotten():
    """Deleting the watchlist row is one of the two ways the owner fixes this.
    If the probe kept complaining about it, the fix would not work and the
    alarm would be permanent for real."""
    clock = [0.0]
    beat = WorkerHeartbeat(clock=lambda: clock[0])
    beat.mark_symbols({"2330.TW"}, set())
    clock[0] = 600.0

    beat.mark_symbols(set(), set())

    assert beat.snapshot().symbol_gap_sec == {}


def test_a_healthy_symbol_is_not_dragged_down_by_a_dead_one():
    clock = [0.0]
    beat = WorkerHeartbeat(clock=lambda: clock[0])

    beat.mark_symbols({"2330.TW", "AAPL"}, {"AAPL"})
    clock[0] = 600.0

    assert set(beat.snapshot().symbol_gap_sec) == {"2330.TW"}


# --- what the probe does with it --------------------------------------------


def _healthz(client, monkeypatch, gaps: dict[str, float]):
    """Runs the endpoint with the worker enabled and a heartbeat that reports
    `gaps`, which is the state a live deployment would be in."""
    from app.services import worker_health

    class _Beat:
        @staticmethod
        def snapshot():
            return worker_health.HeartbeatSnapshot(
                uptime_sec=9999.0,
                last_loop_age_sec=1.0,
                last_poll_age_sec=1.0,
                consecutive_empty_polls=0,
                symbol_gap_sec=gaps,
            )

    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    # conftest turns both of these off so the suite never starts a real loop
    # or sends anything. Left off, every response here would be a 503 for
    # reasons that have nothing to do with symbols, and the status code would
    # stop meaning anything.
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", _Beat())
    # **看門狗看的是深的那一條。** `/healthz` 沒帶參數的時候只回答「重開這台機器有沒有
    # 機會修好」——Render 的健康檢查看的是它，而它失敗 60 秒就會把行程重開（見
    # test_the_probe_render_watches_cannot_restart_him_forever）。這裡問的是「有沒有人
    # 會被通知」，那是 ?deep=1。
    return client.get("/healthz", params={"deep": "1"})


def test_the_probe_is_green_while_every_symbol_prices(client, monkeypatch):
    assert _healthz(client, monkeypatch, {}).status_code == 200


def test_a_brief_gap_does_not_page_anyone(client, monkeypatch):
    """A provider hiccup, or the few minutes a quote is legitimately served
    from cache while a refresh fails. Neither is an outage."""
    resp = _healthz(client, monkeypatch, {"2330.TW": 60.0})

    assert resp.status_code == 200


def test_a_symbol_with_no_price_for_a_long_time_turns_the_probe_red(client, monkeypatch):
    resp = _healthz(client, monkeypatch, {"2330.TW": settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1})

    assert resp.status_code == 503
    assert resp.json()["checks"]["symbols"]["status"] == "fail"


def test_the_response_counts_the_symbols_but_does_not_name_them(client, monkeypatch):
    """THE CONTRACT CHANGED, and the reason is worth keeping written down.

    This used to assert the names, because otherwise the watchdog email says
    「something is wrong」 and leaves the owner to work out which of their rows
    it was. That reasoning still stands -- but this endpoint has no
    authentication and cannot have any: render.yaml points its health check
    here and the external watchdog polls it with no credentials. Naming the
    symbols therefore published the owner's watchlist to anyone who asked, and
    it did so at precisely the moment something had gone wrong.

    A count tells a probe everything a probe can act on. The names are still
    one login away on /api/system/status, which is where the person who has to
    fix the row is going anyway -- see the test below, which exists so nobody
    removes them from there believing they are redundant.
    """
    resp = _healthz(client, monkeypatch, {"2330.TW": 99999.0, "AAPL": 1.0})

    symbols = resp.json()["checks"]["symbols"]
    assert symbols["stale_count"] == 1
    assert "2330.TW" not in resp.text


def test_but_the_owner_can_still_find_out_which_symbol_it_was(auth_client, db_session, monkeypatch):
    """The half of the old contract that must not be lost: 「something is
    wrong」 is not a thing anybody can act on."""
    from app.services import worker_health

    _own_the_symbol(db_session, "2330.TW")

    class _Beat:
        @staticmethod
        def snapshot():
            return worker_health.HeartbeatSnapshot(
                uptime_sec=9999.0,
                last_loop_age_sec=1.0,
                last_poll_age_sec=1.0,
                consecutive_empty_polls=0,
                symbol_gap_sec={"2330.TW": 99999.0},
            )

    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", _Beat())

    body = auth_client.get("/api/system/status").json()

    assert any(row["symbol"] == "2330.TW" for row in body["market_data"]["stale_symbols"])


def test_the_check_is_disabled_along_with_the_worker(client, monkeypatch):
    """No worker means nothing is polling, which the worker check already
    covers. A second failure saying the same thing is noise."""
    monkeypatch.setattr(settings, "WORKER_ENABLED", False)

    assert client.get("/healthz").json()["checks"]["symbols"]["status"] == "disabled"


# --- the email that results --------------------------------------------------


def test_the_watchdog_tells_the_owner_which_symbol_and_what_it_means():
    from scripts.watchdog import read_verdict

    body = (
        '{"status": "fail", "checks": {"symbols": '
        '{"status": "fail", "stale_symbols": ["2330.TW", "6488.TWO"]}}}'
    )

    problems = read_verdict(503, body)

    assert len(problems) == 1
    assert "2330.TW" in problems[0] and "6488.TWO" in problems[0]
    assert "提醒" in problems[0], problems[0]


def test_the_watchdog_still_copes_when_the_names_are_missing():
    """An older deployment answering a newer watchdog. Losing the whole report
    to a KeyError would be worse than a vaguer sentence."""
    from scripts.watchdog import read_verdict

    problems = read_verdict(503, '{"checks": {"symbols": {"status": "fail"}}}')

    assert len(problems) == 1


def _own_the_symbol(db_session, symbol: str) -> None:
    """把那個代號掛在呼叫者名下。

    狀態頁的 stale_symbols 現在只列呼叫者自己的代號——heartbeat 是行程層級的
    單例，它的表是跨全部帳號的聯集，原本等於把別人在看什麼股票攤開給任何一個
    有帳號的人（tests/test_the_status_page_shows_only_your_symbols.py）。
    這個 helper 讓測試植入的代號真的屬於這個帳號，斷言的意思才跟以前一樣。
    """
    from decimal import Decimal

    from app.enums import DataSource
    from app.models.strategy import Strategy
    from app.models.user import User

    owner = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    db_session.add(
        Strategy(
            user_id=owner.id,
            name=f"watches-{symbol}",
            symbol=symbol,
            data_source=DataSource.YFINANCE,
            source_code="class Strategy:" + chr(10) + "    pass" + chr(10),
            code_hash=f"hash-{symbol}",
            default_quantity=Decimal(1),
        )
    )
    db_session.commit()
