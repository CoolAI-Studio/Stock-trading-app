from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.enums import DataSource
from app.models.user import User
from app.schemas.market import QuoteRead
from app.services.market_data.service import MarketDataService, get_market_data_service

router = APIRouter(prefix="/market", tags=["market"])

# /history and /status are deferred until Phase 3+ actually needs them (the
# worker's last-poll bookkeeping and the dashboard's chart panel) -- no point
# shipping untested proxy/aggregation endpoints ahead of a real consumer.


@router.get("/quote", response_model=list[QuoteRead])
def get_quote(
    symbols: str = Query(..., description="Comma-separated symbols, e.g. AAPL,TSLA"),
    data_source: DataSource = DataSource.YFINANCE,
    db: Session = Depends(get_db),
    service: MarketDataService = Depends(get_market_data_service),
    user: User = Depends(get_current_active_user),
) -> list[QuoteRead]:
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No symbols provided"
        )

    quotes = service.get_quotes(symbol_list, data_source)
    service.upsert_quotes(db, quotes)
    return [QuoteRead(**vars(quote)) for quote in quotes.values()]
