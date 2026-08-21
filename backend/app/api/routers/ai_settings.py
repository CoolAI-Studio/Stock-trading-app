"""Turning the AI on, checking it works, and turning it off.

The page that was missing. AI was the only secret in this codebase configured
through an environment variable, so there was nothing in the app to say the
feature existed, no way to add a key without Render's Environment page, and no
way to change one without a redeploy -- Render restarts the service on every
environment change, so correcting a typo in a model name cost a minute of
downtime on the product whose whole promise is not going down.

Everything here follows the pattern the notification channels and broker
credentials already set: encrypted at rest, write-only over the API, and a
button that finds out whether it actually works.
"""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.services import ai_settings
from app.services.ai_provider import get_ai_provider

router = APIRouter(prefix="/ai-settings", tags=["ai"])


class AiSettingsRead(BaseModel):
    """What the page may know. Never the key.

    Write-only over the API, like every other secret on every other settings
    page: a response the browser caches is the wrong place for one.
    """

    configured: bool
    # "database" | "env" | "none". Reported because 「it works and I never set
    # it here」 is confusing enough to send somebody hunting through Render for
    # a value they do not remember typing.
    source: str
    provider: str
    base_url: str
    model: str
    # The last four characters, which is what tells 「the key I meant」 from
    # 「one I pasted wrong six months ago」 without revealing it.
    key_preview: str | None


class AiSettingsWrite(BaseModel):
    provider: str
    base_url: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    # None means 「leave the key alone」. Correcting a model name is the
    # commonest edit by far, and demanding the secret be retyped for it sends
    # somebody to a password manager to change a string that is not secret.
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("provider")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in ai_settings.PROVIDERS:
            # Refused rather than passed through: an unrecognised name falls
            # back to the openai-compatible client and produces a connection
            # error against whatever URL happens to be set, which reads as
            # 「the key is wrong」.
            raise ValueError(f"不認識的 AI 供應者：{value}")
        return value


class AiTestResult(BaseModel):
    ok: bool
    reply: str | None = None
    error: str | None = None


@router.get("", response_model=AiSettingsRead)
def read_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> AiSettingsRead:
    resolved = ai_settings.resolve(db, user.id)
    return AiSettingsRead(
        configured=resolved.is_configured,
        source=resolved.source,
        provider=resolved.provider,
        base_url=resolved.base_url,
        model=resolved.model,
        key_preview=ai_settings.key_preview(resolved.api_key),
    )


@router.put("", response_model=AiSettingsRead)
def write_settings(
    payload: AiSettingsWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> AiSettingsRead:
    ai_settings.save(
        db,
        user.id,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )
    return read_settings(db=db, user=user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    """Stop overriding.

    Falls back to the environment variable if the deployment has one, rather
    than switching the feature off: somebody who saved a row on top of an env
    key expects the env key back, not silence.
    """
    ai_settings.clear(db, user.id)


@router.post("/test", response_model=AiTestResult)
def test_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> AiTestResult:
    """Ask the model one trivial question and report exactly what came back.

    Without this the only way to tell a working key from a wrong one is to use
    a real feature and read its error -- which is how somebody concludes the
    app is broken rather than their key.
    """
    resolved = ai_settings.resolve(db, user.id)
    if not resolved.is_configured:
        return AiTestResult(ok=False, error="還沒設定 AI 金鑰或模型。")

    result = get_ai_provider(resolved).ask(
        "回答「ok」兩個字就好，不要有其他內容。",
        system="You are answering a connectivity check. Reply with exactly: ok",
    )
    return AiTestResult(ok=result.ok, reply=result.reply, error=result.error)
