from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.risk import RiskSettings
from app.models.user import User
from app.schemas.risk import RiskSettingsRead, RiskSettingsUpdate

router = APIRouter(prefix="/risk-settings", tags=["risk"])


def _get_or_create(db: Session, user: User) -> RiskSettings:
    row = db.query(RiskSettings).filter(RiskSettings.user_id == user.id).first()
    if row is None:
        row = RiskSettings(user_id=user.id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=RiskSettingsRead)
def get_risk_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> RiskSettings:
    return _get_or_create(db, user)


@router.put("", response_model=RiskSettingsRead)
def update_risk_settings(
    payload: RiskSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> RiskSettings:
    row = _get_or_create(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row
