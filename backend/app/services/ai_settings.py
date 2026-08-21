"""Which model to ask, and the key to ask it with -- resolved for one user.

AI was the only secret in this codebase that lived in an environment variable.
Telegram tokens, LINE tokens, SMTP passwords and broker credentials are all in
the database, Fernet-encrypted, managed on a page with a 測試 button. AI's
absence from that pattern had exactly the consequences the pattern prevents:

  nothing in the app said the feature existed, so somebody who deployed and
    later wanted it had no way to find out;
  adding it meant Render's Environment page, which the app never mentions;
  CHANGING it meant a redeploy -- Render restarts the service on every
    environment change, so fixing a typo in a model name cost a minute of
    downtime on the product whose whole promise is not going down;
  and there was no way to tell a working key from a wrong one except by asking
    a question somewhere else in the app and reading the error.

PRECEDENCE. The row is an OVERRIDE, not a replacement: a deployment that
already set AI_API_KEY keeps working untouched, and deleting the row falls back
to the environment rather than switching the feature off. 「Delete」 means 「stop
overriding」, which is what somebody who set a row on top of an env key expects.

NOT BUILT: usage metering. The provider's own dashboard counts tokens far
better than this could, and a second, worse copy of that number here would be
read as authoritative. What the app owes the owner is that the key is theirs,
that every question spends it, and a switch to stop.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ai import AiSettings

# The two the providers in services/ai_provider know how to drive. Refused
# rather than passed through: an unrecognised name would fall back to the
# openai-compatible client and produce a connection error against whatever URL
# happened to be there, which reads as 「the key is wrong」.
PROVIDERS = ("openai_compatible", "anthropic")


@dataclass(frozen=True)
class ResolvedAI:
    """The effective settings, and where they came from.

    `source` reaches the page because 「it works and I never set it here」 is
    confusing enough to send somebody hunting through Render for a value they
    do not remember typing.
    """

    provider: str
    base_url: str
    api_key: str
    model: str
    source: str  # "database" | "env" | "none"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip() and self.model.strip())


def _row(db: Session, user_id: int) -> AiSettings | None:
    return db.execute(select(AiSettings).where(AiSettings.user_id == user_id)).scalar_one_or_none()


def resolve(db: Session, user_id: int) -> ResolvedAI:
    row = _row(db, user_id)
    if row is not None:
        return ResolvedAI(
            provider=row.provider,
            base_url=row.base_url,
            api_key=(row.api_key_encrypted or {}).get("api_key", ""),
            model=row.model,
            source="database",
        )

    api_key = (settings.AI_API_KEY or "").strip()
    model = (settings.AI_MODEL or "").strip()
    return ResolvedAI(
        provider=settings.AI_PROVIDER,
        base_url=settings.AI_BASE_URL,
        api_key=api_key,
        model=model,
        source="env" if (api_key and model) else "none",
    )


def save(
    db: Session,
    user_id: int,
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str | None,
) -> AiSettings:
    """Create or update the one row for this user.

    `api_key=None` means 「leave the key alone」. Correcting a model name is the
    commonest edit by far, and requiring the secret to be retyped for it would
    send somebody to a password manager to change a string that is not secret.
    """
    row = _row(db, user_id)
    if row is None:
        row = AiSettings(user_id=user_id, api_key_encrypted={"api_key": ""})
        db.add(row)

    row.provider = provider
    row.base_url = base_url
    row.model = model
    if api_key is not None:
        row.api_key_encrypted = {"api_key": api_key}

    db.commit()
    db.refresh(row)
    return row


def clear(db: Session, user_id: int) -> None:
    """Stop overriding. Falls back to the environment, if there is one."""
    row = _row(db, user_id)
    if row is not None:
        db.delete(row)
        db.commit()


def key_preview(key: str) -> str | None:
    """Enough to recognise which key this is, and no more.

    The tail rather than the head: provider keys share a prefix (`sk-`,
    `sk-or-v1-`), so the first characters identify the provider and nothing
    else. The last four are what tell 「the key I meant」 from 「one I pasted
    wrong six months ago」.
    """
    text = (key or "").strip()
    if not text:
        return None
    return f"…{text[-4:]}" if len(text) > 4 else "…"
