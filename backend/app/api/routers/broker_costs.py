from fastapi import APIRouter, Depends

from app.api.deps import get_current_active_user
from app.models.user import User
from app.schemas.broker_costs import BrokerCostPresetRead
from app.services import broker_costs

# Static data, but behind auth like everything else: an unauthenticated
# endpoint here would be one more thing to reason about for no gain, and the
# form that reads it is already behind a login.
router = APIRouter(prefix="/broker-costs", tags=["broker-costs"])


@router.get("", response_model=list[BrokerCostPresetRead])
def list_broker_costs(
    user: User = Depends(get_current_active_user),
) -> list[BrokerCostPresetRead]:
    """The costs the backtest form's dropdown offers.

    Served rather than hard-coded in the frontend so the rates live next to
    the tests that pin them, and so a rate change is a backend deploy rather
    than a rebuild of the page.
    """
    return [BrokerCostPresetRead.model_validate(preset) for preset in broker_costs.catalogue()]
