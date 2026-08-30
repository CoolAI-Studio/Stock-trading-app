import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.config import settings, vapid_keys
from app.db.session import get_db
from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.schemas.notification import (
    ChannelCreate,
    ChannelRead,
    ChannelTestResult,
    ChannelUpdate,
    EmailConfig,
    LineConfig,
    NotificationLogRead,
    PushReceipt,
    TelegramConfig,
    WebPushConfig,
)
from app.services.notification.base import SendResult
from app.services.notification.dispatcher import SENDERS

router = APIRouter(prefix="/notifications", tags=["notifications"])

_CONFIG_SCHEMAS = {
    ChannelType.TELEGRAM: TelegramConfig,
    ChannelType.LINE: LineConfig,
    ChannelType.EMAIL: EmailConfig,
    ChannelType.WEB_PUSH: WebPushConfig,
}

# Keys whose value gets masked in the read-only preview shown to the client.
_SECRET_KEYS = {"bot_token", "access_token", "password", "auth", "p256dh"}


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _preview(channel_type: ChannelType, config: dict) -> str:
    parts = []
    for key, value in config.items():
        shown = _mask(value) if key in _SECRET_KEYS and isinstance(value, str) else value
        parts.append(f"{key}={shown}")
    return f"{channel_type.value}: " + ", ".join(parts)


def _validate_config(channel_type: ChannelType, config: dict) -> dict:
    schema = _CONFIG_SCHEMAS[channel_type]
    try:
        validated = schema.model_validate(config)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return validated.model_dump()


def _to_read(channel: NotificationChannel) -> ChannelRead:
    read = ChannelRead.model_validate(channel)
    read.config_preview = _preview(channel.channel_type, channel.config_encrypted)
    if channel.channel_type == ChannelType.WEB_PUSH:
        read.push_endpoint = (channel.config_encrypted or {}).get("endpoint")
    return read


def _get_owned_channel(db: Session, user: User, channel_id: int) -> NotificationChannel:
    channel = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.id == channel_id, NotificationChannel.user_id == user.id)
        .first()
    )
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification channel not found"
        )
    return channel


@router.get("/channels", response_model=list[ChannelRead])
def list_channels(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> list[ChannelRead]:
    channels = db.query(NotificationChannel).filter(NotificationChannel.user_id == user.id).all()
    return [_to_read(c) for c in channels]


@router.post("/channels", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> ChannelRead:
    validated_config = _validate_config(payload.channel_type, payload.config)
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=payload.channel_type,
        label=payload.label,
        config_encrypted=validated_config,
        subscribed_events=payload.subscribed_events,
        quiet_start_hour=payload.quiet_start_hour,
        quiet_end_hour=payload.quiet_end_hour,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return _to_read(channel)


@router.patch("/channels/{channel_id}", response_model=ChannelRead)
def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> ChannelRead:
    channel = _get_owned_channel(db, user, channel_id)

    if payload.label is not None:
        channel.label = payload.label
    if payload.is_enabled is not None:
        channel.is_enabled = payload.is_enabled
    if payload.subscribed_events is not None:
        channel.subscribed_events = payload.subscribed_events
    # Sent explicitly on every save, so clearing the window is possible --
    # `is not None` would make it one-way.
    channel.quiet_start_hour = payload.quiet_start_hour
    channel.quiet_end_hour = payload.quiet_end_hour
    if payload.config is not None:
        channel.config_encrypted = _validate_config(channel.channel_type, payload.config)

    db.commit()
    db.refresh(channel)
    return _to_read(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(
    channel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    channel = _get_owned_channel(db, user, channel_id)
    # Keep what this channel FAILED to deliver.
    #
    # The FK cascades, and the app's own repair advice for a dead push
    # subscription is 「請在這台裝置上刪除後重新建立推播管道」. Following that
    # instruction removed every log row for the channel -- including every
    # FAILED row recording an alert that never arrived. The one action the app
    # told the owner to take was the action that erased the proof of what had
    # gone wrong.
    #
    # Done explicitly here as well as in the FK because SQLite does not enforce
    # ondelete by default -- see the same note in api/routers/strategies.py.
    db.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).update(
        {NotificationLog.channel_id: None}, synchronize_session=False
    )
    db.delete(channel)
    db.commit()


@router.post("/channels/{channel_id}/test", response_model=ChannelTestResult)
def test_channel(
    channel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> ChannelTestResult:
    channel = _get_owned_channel(db, user, channel_id)

    if not settings.NOTIFICATIONS_ENABLED:
        # This button exists to answer "will my alerts arrive?". With the
        # dispatcher switched off the answer is no -- but the button sent a
        # real message anyway, reported success, and wrote a green 已送出 row,
        # manufacturing evidence for the opposite of the truth on the one
        # screen built to settle the question.
        return ChannelTestResult(
            ok=False,
            error=(
                "伺服器的 NOTIFICATIONS_ENABLED 設定是關閉的 —— 這個管道本身可能沒問題，"
                "但真正的警告一則都不會送出。請先把伺服器的這個設定打開。"
            ),
            log_id=None,
        )

    sender = SENDERS[channel.channel_type]
    test_message = "這是一則測試通知。看到這則訊息，代表這個管道可以送達。"

    # Only web push can report back -- it is the only channel with a service
    # worker on the other end. Minting a token for Telegram or email would
    # leave the UI waiting for a confirmation that cannot arrive.
    receipt_token = (
        secrets.token_urlsafe(32) if channel.channel_type == ChannelType.WEB_PUSH else None
    )

    try:
        if receipt_token is not None:
            result = sender.send(channel.config_encrypted, test_message, receipt_token)
        else:
            result = sender.send(channel.config_encrypted, test_message)
    except Exception as exc:  # noqa: BLE001 -- see below
        # The one button whose whole job is to tell the owner whether their
        # alerting works must never answer with a 500. An unhandled exception
        # here rendered as a bare "失敗（500）" with no cause, on the screen
        # where the cause is the entire point.
        result = SendResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:500])

    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="test",
        status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
        error=result.error,
        # A token on a send that never left would be redeemable by nothing.
        receipt_token=receipt_token if result.ok else None,
    )
    db.add(log)
    channel.last_sent_at = utcnow()
    channel.last_error = result.error
    db.commit()
    db.refresh(log)

    # `ok` deliberately still means only "the push service accepted it" --
    # RFC 8030 §5 says a 2xx "does not indicate that the message was delivered
    # to the user agent". log_id is how the UI finds out what actually
    # happened.
    return ChannelTestResult(ok=result.ok, error=result.error, log_id=log.id)


@router.post("/push/receipt", status_code=status.HTTP_204_NO_CONTENT)
def push_receipt(payload: PushReceipt, db: Session = Depends(get_db)) -> Response:
    """The service worker confirming it displayed a notification.

    UNAUTHENTICATED ON PURPOSE. A service worker has no access to the app's
    JWT, and it does not need one: RFC 8291 encrypts the push payload end to
    end with keys only that subscription holds -- the push service itself
    cannot read it -- so holding this token IS proof that the intended device
    decrypted the message. Nobody else can forge a receipt, Apple included.

    Always 204, whether or not the token matched. There is nothing the service
    worker could do with a different answer, and saying "no such token" would
    turn this into an oracle for guessing them.
    """
    log = db.query(NotificationLog).filter(NotificationLog.receipt_token == payload.token).first()
    if log is not None and log.delivered_at is None:
        log.delivered_at = utcnow()
        # Single use: a captured receipt must not be replayable to make a
        # later, undelivered alert look delivered.
        log.receipt_token = None
        db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/logs/{log_id}", response_model=NotificationLogRead)
def get_log(
    log_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> NotificationLog:
    """One row, so the UI can poll a specific send for its delivery receipt
    rather than racing the whole log list."""
    log = (
        db.query(NotificationLog)
        .filter(NotificationLog.id == log_id, NotificationLog.user_id == user.id)
        .first()
    )
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log not found")
    return log


@router.get("/logs", response_model=list[NotificationLogRead])
def list_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[NotificationLog]:
    return (
        db.query(NotificationLog)
        .filter(NotificationLog.user_id == user.id)
        .order_by(NotificationLog.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/push/vapid-public-key")
def get_vapid_public_key(user: User = Depends(get_current_active_user)) -> dict:
    """The frontend passes this straight to PushManager.subscribe() as
    applicationServerKey -- public by design, safe to hand to any logged-in
    user (auth is only required here to avoid an unauthenticated GET, not
    because the key itself is sensitive)."""
    # 同一個來源：瀏覽器把這把公鑰烤進它建立的每一個訂閱，而簽名用的是同一對的私
    # 鑰。這裡如果讀 settings 而簽名讀推導的，每一次推播都會被回 403，而 app 全綠。
    public_key, _ = vapid_keys(settings)
    return {"public_key": public_key or ""}


@router.delete("/logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification_log(
    log_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    log = (
        db.query(NotificationLog)
        .filter(NotificationLog.id == log_id, NotificationLog.user_id == user.id)
        .first()
    )
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log entry not found")
    if log.next_retry_at is not None:
        # notification_logs IS the retry queue -- retry.py says so in its own
        # header -- so deleting this row cancels a delivery the owner has not
        # received yet. clear_notification_logs below already guards for
        # exactly this; the single-row delete did not, and offered it as
        # "remove from history".
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "這則通知還沒送出，正在等待重送 —— 刪掉它就等於取消這次通知。"
                "等它送出（或送到放棄）之後就可以刪了。"
            ),
        )
    db.delete(log)
    db.commit()


@router.delete("/logs")
def clear_notification_logs(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> dict[str, int]:
    """Clear the send history.

    Every other log-like table here has a retention sweep; this one never had
    one, so a few channels running for a year put tens of thousands of rows on
    a free-tier database with no way to remove them.

    Rows with a retry still due are kept: those are not history, they are
    notifications the owner has not received yet, and dropping one drops the
    delivery.
    """
    deleted = (
        db.query(NotificationLog)
        .filter(NotificationLog.user_id == user.id, NotificationLog.next_retry_at.is_(None))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}
