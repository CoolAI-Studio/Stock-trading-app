from fastapi import APIRouter, Depends

from app.api.deps import get_current_active_user
from app.config import settings
from app.models.user import User
from app.ws.tickets import issue_ticket

router = APIRouter(prefix="/ws", tags=["ws"])


@router.post("/ticket")
def create_ws_ticket(user: User = Depends(get_current_active_user)) -> dict:
    ticket = issue_ticket(user.id, settings.WS_TICKET_TTL_SECONDS)
    return {"ticket": ticket, "expires_in": settings.WS_TICKET_TTL_SECONDS}
