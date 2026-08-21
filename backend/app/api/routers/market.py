from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.enums import DataSource
from app.models.user import User
from app.schemas.market import (
    AvailableIndicators,
    BarRead,
    BarsRead,
    IndicatorRequest,
    IndicatorSeriesRead,
    IndicatorsRead,
    QuoteRead,
    SourceTimeframes,
    TimeframeOption,
    TimeframesRead,
)
from app.services import chart_indicators, indicator_panes, symbol_search
from app.services.market_data.base import (
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_LABELS,
    Timeframe,
    max_bars_available,
    supports_timeframe,
)
from app.services.market_data.service import (
    DEFAULT_BAR_LIMIT,
    MarketDataService,
    get_market_data_service,
)

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
    # The same depth the market loop asks for, deliberately. Two different
    # defaults mean the chart's cache entry can never answer the loop's
    # question and vice versa, so every chart view costs an extra request on an
    # IP that is already rate limited -- which is how a stock with fifty years
    # of history once read as having none.
    limit: int = Query(default=DEFAULT_BAR_LIMIT, ge=1, le=MAX_CHART_BARS),
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

    _refuse_unsupported(data_source, timeframe)

    bars = service.get_bars(cleaned, timeframe, data_source, limit=limit)
    return BarsRead(
        symbol=cleaned,
        timeframe=timeframe.value,
        fetch_failed=not bars and service.bar_fetch_failed(cleaned, timeframe, data_source),
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


def _refuse_unsupported(data_source: DataSource, timeframe: Timeframe) -> None:
    """A candle this source does not serve is refused before the network.

    Yahoo answers an unsupported interval with an EMPTY FRAME, not an error, so
    without this the request becomes a failed fetch and the page says 「暫時抓
    不到…可能是被限流了」 -- a transient sentence for a permanent condition,
    which sends the reader off to wait for something that will never change.
    """
    if supports_timeframe(data_source, timeframe):
        return
    offered = "、".join(
        TIMEFRAME_LABELS[option] for option in SUPPORTED_TIMEFRAMES.get(data_source, ())
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"{TIMEFRAME_LABELS.get(timeframe, timeframe.value)}"
            f"這個資料來源沒有提供。可以選：{offered}。"
        ),
    )


@router.get("/timeframes", response_model=TimeframesRead)
def list_timeframes(
    user: User = Depends(get_current_active_user),
) -> TimeframesRead:
    """Which candle sizes each source serves, finest first, with a name.

    Served from here rather than listed in the page, for the same reason the
    indicator panes are: a second list in TypeScript drifts the first time an
    interval is added, and the drift shows up as a button that answers 「抓不
    到」 for a candle that was never available.
    """
    return TimeframesRead(
        sources=[
            SourceTimeframes(
                data_source=source,
                timeframes=[
                    TimeframeOption(
                        value=timeframe.value,
                        label=TIMEFRAME_LABELS[timeframe],
                        max_bars=max_bars_available(source, timeframe),
                    )
                    for timeframe in timeframes
                ],
            )
            for source, timeframes in SUPPORTED_TIMEFRAMES.items()
        ]
    )


@router.get("/indicators/available", response_model=AvailableIndicators)
def available_indicators(
    user: User = Depends(get_current_active_user),
) -> AvailableIndicators:
    """What the chart can draw, and which axis each output needs.

    The axis comes from the server because it cannot be derived and must not be
    guessed twice: see services/indicator_panes.py. A client-side rule would be
    a second answer to the same question, and the first time the two disagreed
    the chart would silently squash.
    """
    return AvailableIndicators(indicators=indicator_panes.chartable())


@router.post("/indicators", response_model=IndicatorsRead)
def compute_indicators(
    payload: IndicatorRequest,
    service: MarketDataService = Depends(get_market_data_service),
    user: User = Depends(get_current_active_user),
) -> IndicatorsRead:
    """Indicator values over the same candles the chart is drawing.

    Computed with `spec.fn` -- the very object the strategy sandbox hands to
    user code -- so the line on the chart and the number a strategy trades on
    cannot drift apart. That is the whole reason this is a server endpoint and
    not a TypeScript moving average.
    """
    cleaned = symbol_search.normalise(payload.symbol)
    problem = symbol_search.looks_unpriceable(cleaned)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    _refuse_unsupported(payload.data_source, payload.timeframe)

    bars = service.get_bars(cleaned, payload.timeframe, payload.data_source, limit=payload.limit)
    try:
        series = chart_indicators.compute(
            bars, [request.model_dump() for request in payload.indicators]
        )
    except chart_indicators.IndicatorRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return IndicatorsRead(
        symbol=cleaned,
        timeframe=payload.timeframe.value,
        series=[
            IndicatorSeriesRead(
                name=item.name,
                key=item.key,
                pane=item.pane,
                scale=item.scale,
                points=[{"time": point.time, "value": point.value} for point in item.points],
            )
            for item in series
        ],
    )
