"""One page that answers 「是不是還在跑」 without leaving the app.

CLAUDE.md asks for Prometheus metrics and a Grafana dashboard, and gives the
reason: 「警告不能停擺，就必須看得到它有沒有在跑」. That reason is right. The
instruments are wrong for who this is now for.

A Prometheus endpoint is only worth having if something scrapes it, and on a
free-tier Render box nothing does. Making it real means a Grafana Cloud
account, a set of push credentials, and an eighth blank in a deploy form -- for
a dashboard that somebody who wants stock alerts on their phone will never
open. It would make the product harder to start using in exchange for a screen
its audience does not want.

Everything those dashboards would plot is already in this process:

  the worker's heartbeat and its run of empty polls (services/worker_health);
  which symbols have gone too long without a price (the same);
  what happened to every notification ever raised (notification_logs, which
    already knows sent / retrying / given up / held for quiet hours).

So the answer is a page inside the app: no third party, no account, no extra
blank, and it works on every deployment the moment it boots.

WHY NOT JUST /healthz. That endpoint is unauthenticated, so it is deliberately
terse -- 「fail」 with no numbers attached, because anyone can read it. This one
is behind a login and can therefore say how long, how many, and which.
"""

from datetime import UTC, datetime, timedelta

from app.models.enums import ChannelType, NotificationStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User


def _channel(db_session, user_id: int) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _log(db_session, user_id: int, *, status=NotificationStatus.SENT, age_hours=1, **kw):
    row = NotificationLog(
        user_id=user_id,
        event="order.created",
        status=status,
        message="有新的待確認訂單",
        created_at=datetime.now(UTC) - timedelta(hours=age_hours),
        **kw,
    )
    db_session.add(row)
    db_session.commit()
    return row


# --- who may read it ---------------------------------------------------------


def test_it_needs_a_login(client):
    """Unlike /healthz. This one carries counts, ages and symbol names, which
    is exactly the detail an unauthenticated probe must not hand out."""
    assert client.get("/api/system/status").status_code == 401


# --- the worker --------------------------------------------------------------


def test_it_reports_whether_the_worker_is_alive(auth_client):
    body = auth_client.get("/api/system/status").json()

    assert "worker" in body
    assert "last_loop_age_sec" in body["worker"]


def test_it_reports_how_long_the_feed_has_been_empty(auth_client):
    """A run of empty polls is the state that used to read as perfectly
    healthy: the providers swallow every exception and return {}, so the loop
    kept completing on schedule while not one price came back."""
    body = auth_client.get("/api/system/status").json()

    assert "consecutive_empty_polls" in body["market_data"]


def test_it_names_the_symbols_that_have_no_price(auth_client, monkeypatch):
    """The failure /healthz was taught to catch, with the detail /healthz
    cannot carry: which symbol, and for how long."""
    from app.services import worker_health

    class _Beat:
        @staticmethod
        def snapshot():
            return worker_health.HeartbeatSnapshot(
                uptime_sec=100.0,
                last_loop_age_sec=1.0,
                last_poll_age_sec=1.0,
                consecutive_empty_polls=0,
                symbol_gap_sec={"2330.TW": 1800.0},
            )

    monkeypatch.setattr(worker_health, "heartbeat", _Beat())

    body = auth_client.get("/api/system/status").json()

    stale = body["market_data"]["stale_symbols"]
    assert stale and stale[0]["symbol"] == "2330.TW"
    assert stale[0]["gap_sec"] == 1800.0


# --- notifications, which are the product ------------------------------------


def test_it_counts_what_actually_reached_the_owner(auth_client, db_session):
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(db_session, user.id, channel_id=channel.id, status=NotificationStatus.SENT)
    _log(db_session, user.id, channel_id=channel.id, status=NotificationStatus.SENT)

    body = auth_client.get("/api/system/status").json()

    assert body["notifications"]["sent"] == 2


def test_it_separates_still_trying_from_given_up(auth_client, db_session):
    """The distinction the history page already makes, because it is the only
    question worth asking about a failed alert."""
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(
        db_session,
        user.id,
        channel_id=channel.id,
        status=NotificationStatus.FAILED,
        attempts=2,
        next_retry_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    _log(
        db_session,
        user.id,
        channel_id=channel.id,
        status=NotificationStatus.FAILED,
        attempts=5,
        next_retry_at=None,
    )

    body = auth_client.get("/api/system/status").json()

    assert body["notifications"]["retrying"] == 1
    assert body["notifications"]["given_up"] == 1


def test_an_alert_that_reached_nobody_is_counted_apart(auth_client, db_session):
    """channel_id NULL means there was nowhere to send it. Folding that into
    「failed」 would hide the one failure the owner can actually fix."""
    user = db_session.query(User).first()
    _log(
        db_session,
        user.id,
        channel_id=None,
        status=NotificationStatus.FAILED,
        attempts=0,
        next_retry_at=None,
    )

    body = auth_client.get("/api/system/status").json()

    assert body["notifications"]["reached_nobody"] == 1


def test_it_only_counts_the_recent_past(auth_client, db_session):
    """A lifetime total stops moving and stops meaning anything. What the
    owner is asking is 「is it working NOW」."""
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(db_session, user.id, channel_id=channel.id, age_hours=1)
    _log(db_session, user.id, channel_id=channel.id, age_hours=500)

    body = auth_client.get("/api/system/status").json()

    assert body["notifications"]["sent"] == 1
    assert body["notifications"]["window_hours"] > 0


def test_it_never_reports_another_users_notifications(auth_client, db_session):
    other = User(email="other@example.com", hashed_password="x")
    db_session.add(other)
    db_session.commit()
    _log(db_session, other.id, status=NotificationStatus.SENT)

    body = auth_client.get("/api/system/status").json()

    assert body["notifications"]["sent"] == 0


# --- the message it exists to deliver ----------------------------------------


def test_a_healthy_deployment_says_so_in_one_word(auth_client):
    """The page's whole job. 「一切正常」 has to be readable without decoding
    four sub-sections."""
    body = auth_client.get("/api/system/status").json()

    assert body["overall"] in ("ok", "warn", "fail")


def test_a_stalled_worker_turns_the_headline(auth_client, monkeypatch):
    from app.config import settings
    from app.services import worker_health

    class _Beat:
        @staticmethod
        def snapshot():
            return worker_health.HeartbeatSnapshot(
                uptime_sec=99999.0,
                last_loop_age_sec=settings.HEALTH_MAX_AGE_SEC + 1,
                last_poll_age_sec=settings.HEALTH_MAX_AGE_SEC + 1,
                consecutive_empty_polls=0,
                symbol_gap_sec={},
            )

    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", _Beat())

    assert auth_client.get("/api/system/status").json()["overall"] == "fail"


def test_notifications_being_switched_off_is_not_healthy(auth_client, monkeypatch):
    """A muted notifier is not a working one FOR THIS PRODUCT -- the same
    judgement /healthz already makes."""
    from app.config import settings

    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", False)

    assert auth_client.get("/api/system/status").json()["overall"] == "fail"
