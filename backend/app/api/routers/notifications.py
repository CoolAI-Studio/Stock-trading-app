from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.enums import ChannelType, NotificationStatus
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
    TelegramConfig,
)
from app.services.notification.dispatcher import SENDERS

router = APIRouter(prefix="/notifications", tags=["notifications"])

_CONFIG_SCHEMAS = {
    ChannelType.TELEGRAM: TelegramConfig,
    ChannelType.LINE: LineConfig,
    ChannelType.EMAIL: EmailConfig,
}

# Keys whose value gets masked in the read-only preview shown to the client.
_SECRET_KEYS = {"bot_token", "access_token", "password"}


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
    db.delete(channel)
    db.commit()


@router.post("/channels/{channel_id}/test", response_model=ChannelTestResult)
def test_channel(
    channel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> ChannelTestResult:
    channel = _get_owned_channel(db, user, channel_id)
    sender = SENDERS[channel.channel_type]
    test_message = "This is a test notification from your trading app."
    result = sender.send(channel.config_encrypted, test_message)

    db.add(
        NotificationLog(
            user_id=user.id,
            channel_id=channel.id,
            event="test",
            status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
            error=result.error,
        )
    )
    channel.last_sent_at = utcnow()
    channel.last_error = result.error
    db.commit()

    return ChannelTestResult(ok=result.ok, error=result.error)


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
