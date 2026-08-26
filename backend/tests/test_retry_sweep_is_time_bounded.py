"""The retry sweep runs inside the market tick, so its cost is the tick's cost.

market_loop.tick_once calls retry_pending() at the end of every poll, and the
comment above that call states the reason it is safe to do so:

    「It is bounded (one indexed query, at most a handful of sends) so it cannot
     push the stop-loss checks above it off schedule.」

The bound was on the number of rows -- _MAX_PER_SWEEP = 20 -- and a count is not
a bound on time. Every sender in this app uses a 10 second network timeout, so
twenty rows that time out is 200 seconds spent inside one tick. The tick runs
on a single worker thread; nothing else polls a price or checks a stop-loss
while it is in there.

And the sweep only runs at all when sends have been FAILING. A timeout is the
commonest failure it exists to handle, so the pathological case is not exotic:
it is the ordinary case. A phone off the network, an SMTP host that black-holes
connections, a push service being slow -- any of those turned the retry
mechanism into three minutes of the market going unwatched, once per poll, for
as long as the condition lasted.

So the sweep gets a wall-clock budget as well. Rows it did not reach keep their
past-due next_retry_at and are simply first in line next tick -- nothing is
dropped, and no state has to be written to arrange that.
"""

from datetime import timedelta

from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.services.notification import retry
from app.services.notification.base import SendResult


def _user(db_session) -> User:
    user = User(email="sweepclock@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _queue(db_session, user, channel, count: int) -> None:
    for _ in range(count):
        db_session.add(
            NotificationLog(
                user_id=user.id,
                channel_id=channel.id,
                event="order.created",
                status=NotificationStatus.FAILED,
                error="Telegram timed out",
                message="有新的待確認訂單：AAPL 買進 10",
                attempts=1,
                next_retry_at=utcnow() - timedelta(seconds=1),
            )
        )
    db_session.commit()


class _SlowSender:
    """Every send costs `cost` on the fake clock, exactly like a network
    timeout costs ten real seconds."""

    def __init__(self, clock: list[float], cost: float, result: SendResult) -> None:
        self.clock = clock
        self.cost = cost
        self.result = result
        self.calls = 0

    def send(self, _config, _message) -> SendResult:
        self.calls += 1
        self.clock[0] += self.cost
        return self.result


def _sweep(db_session, monkeypatch, sender) -> int:
    monkeypatch.setitem(retry.SENDERS, ChannelType.TELEGRAM, sender)
    return retry.retry_pending(db_session, clock=lambda: sender.clock[0])


# --- the bound the comment claimed ------------------------------------------


def test_a_sweep_of_timeouts_stops_before_it_eats_the_tick(db_session, monkeypatch):
    """Twenty ten-second timeouts is 200 seconds with no price polled and no
    stop-loss checked."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _queue(db_session, user, channel, retry._MAX_PER_SWEEP)
    sender = _SlowSender([0.0], cost=10.0, result=SendResult(ok=False, error="timed out"))

    _sweep(db_session, monkeypatch, sender)

    assert sender.calls * 10.0 <= retry._MAX_SWEEP_SEC + 10.0, (
        f"{sender.calls} sends at 10s each is {sender.calls * 10}s inside one tick"
    )
    assert sender.calls < retry._MAX_PER_SWEEP


def test_the_rows_it_did_not_reach_are_still_owed(db_session, monkeypatch):
    """Nothing may be dropped to make the budget. They are past due already,
    so being left alone IS being first in line next tick."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _queue(db_session, user, channel, retry._MAX_PER_SWEEP)
    sender = _SlowSender([0.0], cost=10.0, result=SendResult(ok=False, error="timed out"))

    _sweep(db_session, monkeypatch, sender)

    untouched = (
        db_session.query(NotificationLog)
        .filter(NotificationLog.attempts == 1, NotificationLog.next_retry_at.isnot(None))
        .count()
    )
    assert untouched > 0


def test_the_next_sweep_picks_up_where_this_one_stopped(db_session, monkeypatch):
    user = _user(db_session)
    channel = _channel(db_session, user)
    _queue(db_session, user, channel, retry._MAX_PER_SWEEP)
    sender = _SlowSender([0.0], cost=10.0, result=SendResult(ok=False, error="timed out"))

    _sweep(db_session, monkeypatch, sender)
    first_round = sender.calls
    sender.clock[0] = 0.0
    _sweep(db_session, monkeypatch, sender)

    assert sender.calls > first_round


# --- and the ordinary case must not be slowed down --------------------------


def test_a_sweep_of_healthy_sends_still_clears_the_whole_batch(db_session, monkeypatch):
    """A working channel answers in milliseconds. The budget must never be
    what limits a sweep that is going fine -- the count cap still is."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _queue(db_session, user, channel, retry._MAX_PER_SWEEP)
    sender = _SlowSender([0.0], cost=0.2, result=SendResult(ok=True, error=None))

    delivered = _sweep(db_session, monkeypatch, sender)

    assert sender.calls == retry._MAX_PER_SWEEP
    assert delivered == retry._MAX_PER_SWEEP


def test_one_row_is_always_attempted(db_session, monkeypatch):
    """A single send that blows the whole budget on its own must still happen.
    If the budget could veto the FIRST attempt, a queue would sit there
    forever without one row ever being tried -- the sweep would have stopped
    retrying while looking like it was running every tick."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _queue(db_session, user, channel, 3)
    sender = _SlowSender(
        [0.0], cost=retry._MAX_SWEEP_SEC * 5, result=SendResult(ok=False, error="timed out")
    )

    _sweep(db_session, monkeypatch, sender)

    assert sender.calls == 1


def test_cutting_a_sweep_short_is_recorded(db_session, monkeypatch, caplog):
    """A sweep that is permanently over budget means a queue that is never
    fully drained. That has to be visible somewhere other than in the
    symptoms."""
    import logging

    user = _user(db_session)
    channel = _channel(db_session, user)
    _queue(db_session, user, channel, retry._MAX_PER_SWEEP)
    sender = _SlowSender([0.0], cost=10.0, result=SendResult(ok=False, error="timed out"))

    with caplog.at_level(logging.WARNING):
        _sweep(db_session, monkeypatch, sender)

    assert any("sweep" in record.message for record in caplog.records)
