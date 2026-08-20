"""Two ways the record of a missed alert was destroyed, both by ordinary use.

## Deleting one log row cancelled an undelivered notification

notification_logs IS the retry queue -- retry.py says so in its own header. So
"delete this row from the history" and "cancel this pending delivery" were the
same operation, and the UI offered it as the former. The function immediately
below it already knew: clear_notification_logs explicitly keeps rows with a
retry still due, "because those are not history, they are notifications the
owner has not received yet". The single-row delete had no such guard.

## Deleting a broken channel destroyed the evidence that it was broken

NotificationLog.channel_id cascades on delete. And the app's own repair advice,
written by _permanent_explanation, is 「請在這台裝置上刪除後重新建立推播管道」.
Follow that instruction and Postgres removes every log row for that channel --
including every FAILED row recording an alert that never arrived. The one
action the app told the owner to take was the action that erased the proof of
what had gone wrong.

Now that channel_id is nullable (an alert can reach no channel at all), those
rows can simply outlive their channel.
"""

from datetime import timedelta

from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog


def _user_id(db_session) -> int:
    from app.models.user import User

    return db_session.query(User).first().id


def _channel(db_session, user_id: int) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=ChannelType.TELEGRAM,
        label="tg",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _log(db_session, user_id: int, channel_id: int, *, due: bool) -> NotificationLog:
    log = NotificationLog(
        user_id=user_id,
        channel_id=channel_id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="HTTP 500",
        message="2330.TW 跌破 950",
        attempts=1,
        next_retry_at=utcnow() + timedelta(minutes=5) if due else None,
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


# --- deleting a row must not cancel a delivery ------------------------------


def test_a_pending_notification_cannot_be_deleted_from_the_history(auth_client, db_session):
    """It is not history. It is an alert the owner has not received yet, and
    the row IS the queue entry."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = _log(db_session, user_id, channel.id, due=True)

    resp = auth_client.delete(f"/api/notifications/logs/{log.id}")

    assert resp.status_code == 409, resp.text
    assert db_session.get(NotificationLog, log.id) is not None


def test_the_refusal_explains_itself(auth_client, db_session):
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = _log(db_session, user_id, channel.id, due=True)

    resp = auth_client.delete(f"/api/notifications/logs/{log.id}")

    assert "還沒送出" in resp.text or "尚未" in resp.text, resp.text


def test_a_finished_row_deletes_normally(auth_client, db_session):
    """The ordinary case has to keep working, or the guard is just an
    obstruction."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = _log(db_session, user_id, channel.id, due=False)

    assert auth_client.delete(f"/api/notifications/logs/{log.id}").status_code == 204
    assert db_session.get(NotificationLog, log.id) is None


# --- deleting a channel must not erase what it failed to deliver ------------


def test_deleting_a_channel_keeps_its_failure_record(auth_client, db_session):
    """The app's own advice for a dead push subscription is to delete the
    channel and make a new one. Doing that used to erase every record of the
    alerts it had failed to deliver -- the fix destroyed the evidence."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = _log(db_session, user_id, channel.id, due=False)

    assert auth_client.delete(f"/api/notifications/channels/{channel.id}").status_code == 204

    db_session.expire_all()
    kept = db_session.get(NotificationLog, log.id)
    assert kept is not None, "the record of what never arrived went with the channel"


def test_the_kept_record_no_longer_points_at_a_channel(auth_client, db_session):
    """It has to be readable afterwards, which means not dangling: channel_id
    becomes NULL, the same shape as an alert that reached nobody."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = _log(db_session, user_id, channel.id, due=False)

    auth_client.delete(f"/api/notifications/channels/{channel.id}")

    db_session.expire_all()
    assert db_session.get(NotificationLog, log.id).channel_id is None


def test_the_log_list_still_renders_after_its_channel_is_gone(auth_client, db_session):
    """A 500 on the history page would be a worse outcome than the cascade."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    _log(db_session, user_id, channel.id, due=False)
    auth_client.delete(f"/api/notifications/channels/{channel.id}")

    resp = auth_client.get("/api/notifications/logs")

    assert resp.status_code == 200
    assert resp.json()[0]["channel_id"] is None
