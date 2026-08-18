from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.strategy import StrategyAlert
from app.models.user import User
from app.schemas.alert import StrategyAlertRead

# Its own top-level resource rather than /strategies/{id}/alerts: the owner's
# main use is scanning every watch-only strategy's recent calls in one list,
# and ?strategy_id= still narrows it to one when they want to score it.
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[StrategyAlertRead])
def list_alerts(
    strategy_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[StrategyAlert]:
    query = db.query(StrategyAlert).filter(StrategyAlert.user_id == user.id)
    if strategy_id is not None:
        query = query.filter(StrategyAlert.strategy_id == strategy_id)
    return query.order_by(StrategyAlert.id.desc()).offset(offset).limit(limit).all()
