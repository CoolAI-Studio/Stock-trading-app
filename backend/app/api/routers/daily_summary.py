from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.daily_summary import DailySummaryRead, DailySummaryUpdate
from app.services import daily_summary

router = APIRouter(prefix="/daily-summary", tags=["daily-summary"])


@router.get("", response_model=DailySummaryRead)
def read_daily_summary(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> dict:
    """收盤摘要的狀態（#117）。

    不收代號：摘要的清單就是自選股，要加哪一檔用自選股那支本來就在驗代號的端點。兩條驗證
    路線會各自漂移，而其中一條放行「台積電」這種永遠沒有報價的字，就是一則每天少一檔的摘要。
    """
    return daily_summary.state_for(db, user.id)


@router.put("", response_model=DailySummaryRead)
def update_daily_summary(
    payload: DailySummaryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    daily_summary.set_enabled(db, user.id, payload.is_enabled)
    return daily_summary.state_for(db, user.id)
