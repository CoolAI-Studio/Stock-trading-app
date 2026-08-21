from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.enums import DataSource
from app.models.user import User
from app.schemas.market import BarRead, BarsRead, QuoteRead
from app.services import symbol_search
from app.services.market_data.base import Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service

router = APIRouter(prefix="/market", tags=["market"])

# /status is still deferred until something needs it. /bars is not: the
# dashboard's chart is a real consumer, and the reason it exists is that
# TradingView's free embedded widget answers 「此商品僅在 TradingView 上可用」 for
# Taiwanese symbols -- its own words for 「the symbol is real, but this widget
# is not licensed to show its data」. No amount of symbol correctness reaches a
# licensing restriction, and this backend already has the candles.

# A chart is a picture, not a backtest. Enough candles for years of daily bars
# and nowhere near MAX_BACKTEST_BARS: yfinance is an unofficial scraper, and
# serving a request for a hundred thousand candles costs the deployment its
# access to it.
MAX_CHART_BARS = 1000


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


@router.get("/bars", response_model=BarsRead)
def get_bars(
    symbol: str = Query(..., min_length=1, max_length=32),
    timeframe: Timeframe = Timeframe.DAY_1,
    limit: int = Query(default=250, ge=1, le=MAX_CHART_BARS),
    data_source: DataSource = DataSource.YFINANCE,
    service: MarketDataService = Depends(get_market_data_service),
    user: User = Depends(get_current_active_user),
) -> BarsRead:
    """Candles for one symbol, oldest first.

    Goes through MarketDataService rather than a provider directly, which is
    what keeps this safe to put behind a page: get_bars() is the same
    rate-limited, per-symbol-per-timeframe cached path the market loop uses, so
    a second page view costs nothing. Straight to the provider, a chart would
    re-download years of candles on every visit and get the deployment's IP
    blocked.
    """
    cleaned = symbol_search.normalise(symbol)
    problem = symbol_search.looks_unpriceable(cleaned)
    if problem:
        # Refused before the network. 「台積電」 and a bare 「2330」 are the two
        # shapes this app already refuses everywhere else -- one is a wasted
        # request against a rate limiter, and the other would come back with a
        # Japanese company's price history and draw it convincingly.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    bars = service.get_bars(cleaned, timeframe, data_source, limit=limit)
    return BarsRead(
        symbol=cleaned,
        timeframe=timeframe.value,
        # An empty list is a real answer, not an error: a newly listed stock
        # genuinely has no candles yet, and 500ing over it would make the page
        # look broken rather than empty.
        bars=[
            BarRead(
                time=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in bars
        ],
    )
