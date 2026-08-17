from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BrokerCredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    broker_name: str = Field(min_length=1, max_length=120)
    config: dict = Field(min_length=1)


class BrokerCredentialUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    broker_name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None


class BrokerCredentialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    broker_name: str
    created_at: datetime
    # Masked, populated by the router -- config_encrypted itself is never
    # part of this model. Every value is masked regardless of key name
    # (unlike notification channels, config here is free-form/unknown
    # shape, so there's no fixed allowlist of "this key is a secret").
    config_preview: str = ""


class AiAssistRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AiAssistResult(BaseModel):
    ok: bool
    reply: str | None = None
    error: str | None = None
