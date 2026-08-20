from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChannelType
from app.schemas.common import UtcDatetime


class TelegramConfig(BaseModel):
    bot_token: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)


class LineConfig(BaseModel):
    access_token: str = Field(min_length=1)
    to: str = Field(min_length=1)


class EmailConfig(BaseModel):
    host: str = Field(min_length=1)
    port: int = 587
    username: str | None = None
    password: str | None = None
    from_addr: str = Field(min_length=1)
    to_addr: str = Field(min_length=1)
    use_tls: bool = True


class WebPushConfig(BaseModel):
    """A browser's PushSubscription, as returned by
    PushManager.subscribe() -- endpoint plus the two keys needed to encrypt
    a push payload for that specific browser/device."""

    endpoint: str = Field(min_length=1)
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class ChannelCreate(BaseModel):
    channel_type: ChannelType
    label: str = Field(min_length=1, max_length=120)
    config: dict
    subscribed_events: list[str] | None = None
    # Hours in the owner's own timezone during which this channel stays quiet.
    # Both None means always on; a notification raised inside the window is
    # held and delivered when it ends, never dropped.
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)


class ChannelUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None
    is_enabled: bool | None = None
    subscribed_events: list[str] | None = None
    # Hours in the owner's own timezone during which this channel stays quiet.
    # Both None means always on; a notification raised inside the window is
    # held and delivered when it ends, never dropped.
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_type: ChannelType
    label: str
    is_enabled: bool
    subscribed_events: list[str] | None
    quiet_start_hour: int | None
    quiet_end_hour: int | None
    last_sent_at: UtcDatetime | None
    last_error: str | None
    # Masked, populated by the router -- config_encrypted itself is never
    # part of this model, so there is no field a bug could accidentally
    # serialize a raw secret through.
    config_preview: str = ""
    # web_push only, and populated by the router for the same reason. The
    # endpoint is not a secret the way p256dh/auth are -- nothing can be
    # delivered with it alone, since a push must be encrypted with those keys
    # and signed with the server's VAPID pair -- and the browser needs it to
    # answer one question it cannot otherwise answer: is this row THIS device?
    # Without it, deleting a channel unsubscribed whichever browser happened
    # to be doing the deleting, so tidying up a stale iPhone row from a laptop
    # silently killed the laptop.
    push_endpoint: str | None = None


class ChannelTestResult(BaseModel):
    ok: bool
    error: str | None = None
    # Which log row to watch for a delivery receipt. `ok` only means the push
    # service accepted the message -- RFC 8030 §5 says so in as many words --
    # so the UI polls this row to find out whether the device ever confirmed
    # displaying it.
    log_id: int | None = None


class PushReceipt(BaseModel):
    """What the service worker posts back after it has shown a notification."""

    token: str = Field(min_length=1, max_length=64)


class NotificationLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    order_id: int | None
    event: str
    status: str
    error: str | None
    created_at: UtcDatetime
    # When the device confirmed it had displayed the notification, or null.
    # `status == sent` only ever meant the push service accepted the message
    # (RFC 8030 §5), which is a much weaker claim than it reads as.
    # receipt_token is deliberately NOT here: it is a bearer credential for one
    # confirmation, and a response the browser caches is the wrong place for it.
    delivered_at: UtcDatetime | None = None
