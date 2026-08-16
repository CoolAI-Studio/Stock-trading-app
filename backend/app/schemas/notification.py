from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChannelType


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


class ChannelCreate(BaseModel):
    channel_type: ChannelType
    label: str = Field(min_length=1, max_length=120)
    config: dict
    subscribed_events: list[str] | None = None


class ChannelUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None
    is_enabled: bool | None = None
    subscribed_events: list[str] | None = None


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_type: ChannelType
    label: str
    is_enabled: bool
    subscribed_events: list[str] | None
    last_sent_at: datetime | None
    last_error: str | None
    # Masked, populated by the router -- config_encrypted itself is never
    # part of this model, so there is no field a bug could accidentally
    # serialize a raw secret through.
    config_preview: str = ""


class ChannelTestResult(BaseModel):
    ok: bool
    error: str | None = None


class NotificationLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    order_id: int | None
    event: str
    status: str
    error: str | None
    created_at: datetime
