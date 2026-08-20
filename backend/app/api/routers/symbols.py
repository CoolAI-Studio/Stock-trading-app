"""Looking a stock up by the name a person actually knows it by.

There was no lookup of any kind: every symbol field in the app was a free-text
box that had to be filled with 「2330.TW」 by someone who already knew that was
the required form. See services/symbol_search for what the two natural inputs
(「台積電」 and 「2330」) did instead -- one silently never priced, the other
priced the wrong company.

Authenticated like everything else, and deliberately reads nothing per-user:
the answer depends only on the bundled table, so it is the same for everybody
and cheap enough to call on every keystroke.
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_active_user
from app.models.user import User
from app.schemas.symbol import SymbolSearchResponse
from app.services import symbol_search

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("/search", response_model=SymbolSearchResponse)
def search_symbols(
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=8, ge=1, le=25),
    _user: User = Depends(get_current_active_user),
) -> SymbolSearchResponse:
    return SymbolSearchResponse(
        query=q,
        matches=symbol_search.search(q, limit=limit),
        # So the UI can explain an absent result rather than just showing
        # nothing: a company listed after this table was built genuinely will
        # not be here, and that is a different problem from a typo.
        listings_generated_at=symbol_search.listings_generated_at(),
    )
