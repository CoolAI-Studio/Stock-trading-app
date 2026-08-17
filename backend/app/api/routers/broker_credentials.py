from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.broker import BrokerCredential
from app.models.user import User
from app.schemas.broker import (
    AiAssistRequest,
    AiAssistResult,
    BrokerCredentialCreate,
    BrokerCredentialRead,
    BrokerCredentialUpdate,
)
from app.services.ai_provider import get_ai_provider

router = APIRouter(prefix="/broker-credentials", tags=["broker-credentials"])


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _preview(config: dict) -> str:
    parts = []
    for key, value in config.items():
        shown = _mask(value) if isinstance(value, str) else value
        parts.append(f"{key}={shown}")
    return ", ".join(parts)


def _to_read(credential: BrokerCredential) -> BrokerCredentialRead:
    read = BrokerCredentialRead.model_validate(credential)
    read.config_preview = _preview(credential.config_encrypted)
    return read


def _get_owned_credential(db: Session, user: User, credential_id: int) -> BrokerCredential:
    credential = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.id == credential_id, BrokerCredential.user_id == user.id)
        .first()
    )
    if credential is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return credential


@router.get("", response_model=list[BrokerCredentialRead])
def list_credentials(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> list[BrokerCredentialRead]:
    credentials = (
        db.query(BrokerCredential).filter(BrokerCredential.user_id == user.id).all()
    )
    return [_to_read(c) for c in credentials]


@router.post("", response_model=BrokerCredentialRead, status_code=status.HTTP_201_CREATED)
def create_credential(
    payload: BrokerCredentialCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> BrokerCredentialRead:
    credential = BrokerCredential(
        user_id=user.id,
        label=payload.label,
        broker_name=payload.broker_name,
        config_encrypted=payload.config,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return _to_read(credential)


@router.patch("/{credential_id}", response_model=BrokerCredentialRead)
def update_credential(
    credential_id: int,
    payload: BrokerCredentialUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> BrokerCredentialRead:
    credential = _get_owned_credential(db, user, credential_id)

    if payload.label is not None:
        credential.label = payload.label
    if payload.broker_name is not None:
        credential.broker_name = payload.broker_name
    if payload.config is not None:
        credential.config_encrypted = payload.config

    db.commit()
    db.refresh(credential)
    return _to_read(credential)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    credential_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    credential = _get_owned_credential(db, user, credential_id)
    db.delete(credential)
    db.commit()


@router.post("/ai-assist", response_model=AiAssistResult)
def ai_assist(
    payload: AiAssistRequest, user: User = Depends(get_current_active_user)
) -> AiAssistResult:
    """Setup guidance only -- see app/services/ai_provider/base.py's system
    prompt. Never touches BrokerCredential rows or order placement."""
    result = get_ai_provider().ask(payload.message)
    return AiAssistResult(ok=result.ok, reply=result.reply, error=result.error)
