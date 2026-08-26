from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.api.routers.market import _refuse_unsupported
from app.db.session import get_db
from app.enums import DataSource
from app.models.backtest import BacktestRun
from app.models.strategy import DEFAULT_WARMUP_BARS, Strategy
from app.models.user import User
from app.schemas.backtest import (
    BacktestResultRead,
    BacktestRunDetail,
    BacktestRunRead,
    BacktestRunRequest,
)
from app.services import strategy_pool
from app.services.backtest import (
    MAX_BACKTEST_BARS,
    BacktestError,
    BacktestResult,
    estimated_bar_count,
    load_backtest_bars,
    run_backtest,
    truncation_note,
)
from app.services.market_data.base import DEFAULT_TIMEFRAME, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service
from app.services.risk_resolver import get_or_create_global, resolve
from app.services.strategy_runtime import code_hash
from app.services.strategy_worker import StrategyWorkerError

router = APIRouter(prefix="/backtests", tags=["backtests"])

# How many runs one user keeps. Each row stores a full equity curve and trade
# list, so an unpruned history is the one part of this feature that grows
# without bound on a free-tier database. Thirty is far more than the owner will
# compare at once, and the newest are always the ones kept.
MAX_RUNS_PER_USER = 30


def _resolve_strategy(db: Session, user: User, strategy_id: int) -> Strategy:
    strategy = (
        db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user.id).first()
    )
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return strategy


def _resolve_timeframe(
    requested: Timeframe | None,
    entry_point: str,
    declared: Timeframe,
    data_source: DataSource,
) -> Timeframe:
    """Which candle size the replay runs at.

    For an on_bar strategy the answer is never the request's: `self.timeframe`
    is what the live loop fetches for it, so replaying the same code on a
    different candle would score behaviour the owner cannot actually run. A
    conflicting request is refused rather than silently resolved either way --
    whichever side lost, the owner would be reading a number about the other.

    An on_tick strategy declares no candle at all (live, it is driven by
    quotes), so there the request is the only thing that can choose one.
    """
    if entry_point == "on_bar":
        if requested is not None and requested is not declared:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"這是 on_bar 策略，K 棒週期由程式碼裡的 self.timeframe = "
                    f'"{declared.value}" 決定，不能在回測時改成 "{requested.value}"。'
                    "要換週期請改策略原始碼。"
                ),
            )
        _refuse_unsupported(data_source, declared)
        return declared
    chosen = requested or DEFAULT_TIMEFRAME
    # 同一道閘門，跟 market.py 的兩個端點和策略的建立／修改用的是同一支函式。
    # 沒有它的時候，Yahoo 對一個不支援的 interval 回的是「空的 frame」而不是
    # 錯誤——所以請求不會壞，它會成功地測完零根 K 棒，然後回一份讀起來像
    # 「這個策略不會進場」的報告。看起來完成、實際上什麼都沒測，是這裡最糟的
    # 輸出。
    _refuse_unsupported(data_source, chosen)
    return chosen


def _resolve_exit_thresholds(
    db: Session, user: User, payload: BacktestRunRequest, strategy: Strategy | None
) -> tuple[Decimal, Decimal]:
    """The stop-loss and take-profit the replay will actually enforce.

    Defaulting to the strategy's own resolved settings rather than to zero is
    the whole point. Live, market_loop._check_position_exit files a SELL the
    moment either threshold is crossed, using exactly these numbers -- so a
    replay that quietly used zero was scoring a strategy that rides every loss
    to the bottom, and calling the result evidence about the one the owner
    runs.

    An explicit value in the request still wins, INCLUDING an explicit 0:
    "what would this have done without my stop" is a question worth being able
    to ask, and it is unaskable if 0 and "unspecified" mean the same thing.
    That is the same three-state rule risk_resolver enforces everywhere else.
    """
    limits = resolve(get_or_create_global(db, user.id), strategy)
    return (
        limits.stop_loss_pct if payload.stop_loss_pct is None else payload.stop_loss_pct,
        limits.take_profit_pct if payload.take_profit_pct is None else payload.take_profit_pct,
    )


def _guard_range(payload: BacktestRunRequest, timeframe: Timeframe) -> None:
    """Refuse an oversized run before a single candle is fetched.

    The shape the owner will actually hit is a long range on a small candle: a
    year of 1-minute bars is over half a million of them, and this process is
    also running the live market loop on a free-tier box.
    """
    estimated = estimated_bar_count(payload.start, payload.end, timeframe)
    if estimated > MAX_BACKTEST_BARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"回測區間太長：{timeframe.value} K 棒在這段期間大約有 {estimated} 根，"
                f"超過單次回測上限 {MAX_BACKTEST_BARS} 根。請縮短區間，"
                "或改用比較大的 K 棒週期。"
            ),
        )


def _persist(
    db: Session,
    user: User,
    payload: BacktestRunRequest,
    strategy: Strategy | None,
    source_code: str,
    symbol: str,
    timeframe: Timeframe,
    data_source: DataSource,
    result: BacktestResult,
) -> BacktestRun:
    serialized = BacktestResultRead.model_validate(result).model_dump(mode="json")
    run = BacktestRun(
        user_id=user.id,
        strategy_id=None if strategy is None else strategy.id,
        strategy_name=result.strategy_name,
        symbol=symbol,
        timeframe=timeframe.value,
        data_source=data_source,
        range_start=payload.start,
        range_end=payload.end,
        source_code=source_code,
        code_hash=code_hash(source_code),
        assumptions=serialized["assumptions"],
        summary=serialized["summary"],
        result=serialized,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    _prune_old_runs(db, user.id)
    return run


def _prune_old_runs(db: Session, user_id: int) -> None:
    keep = [
        row.id
        for row in db.query(BacktestRun.id)
        .filter(BacktestRun.user_id == user_id)
        .order_by(BacktestRun.id.desc())
        .limit(MAX_RUNS_PER_USER)
        .all()
    ]
    db.query(BacktestRun).filter(
        BacktestRun.user_id == user_id, BacktestRun.id.notin_(keep)
    ).delete(synchronize_session=False)
    db.commit()


@router.post("", response_model=BacktestRunDetail, status_code=status.HTTP_201_CREATED)
def create_backtest(
    payload: BacktestRunRequest,
    db: Session = Depends(get_db),
    service: MarketDataService = Depends(get_market_data_service),
    user: User = Depends(get_current_active_user),
) -> BacktestRun:
    """Replay a saved strategy, or a draft's source, over historical candles."""
    strategy = (
        None if payload.strategy_id is None else _resolve_strategy(db, user, payload.strategy_id)
    )
    source_code = payload.source_code if strategy is None else strategy.source_code

    # Compiled here as well as inside run_backtest, deliberately. It is what
    # turns bad source into a 422 the owner can read *before* a history fetch
    # is spent on it, and it is the only way to learn which entry point and
    # candle size the code chose -- both of which decide what to fetch. The
    # second compile inside the replay is microseconds, and a strategy
    # instance must be fresh per run anyway so no state leaks between them.
    try:
        # 子行程裡編（#18）：編譯會執行類別主體和 __init__，所以「只是先編一次看
        # 看」跟跑使用者的程式碼是同一件事。
        described = strategy_pool.describe(source_code)
    except StrategyWorkerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"這份策略程式碼無法通過驗證，因此不能回測：{exc}",
        ) from exc

    symbol = payload.symbol or (strategy.symbol if strategy else described["symbol"])
    data_source = payload.data_source or (strategy.data_source if strategy else DataSource.YFINANCE)
    timeframe = _resolve_timeframe(
        payload.timeframe,
        described["entry_point"],
        Timeframe(described["timeframe"]),
        data_source,
    )
    stop_loss_pct, take_profit_pct = _resolve_exit_thresholds(db, user, payload, strategy)
    _guard_range(payload, timeframe)

    bars = load_backtest_bars(
        service,
        symbol=symbol,
        timeframe=timeframe,
        data_source=data_source,
        start=payload.start,
        end=payload.end,
    )

    stored_warmup = payload.warmup_bars
    if stored_warmup is None:
        stored_warmup = strategy.warmup_bars if strategy else DEFAULT_WARMUP_BARS

    try:
        result = run_backtest(
            source_code=source_code,
            bars=bars,
            symbol=symbol,
            timeframe=timeframe,
            assumptions=payload.to_assumptions(
                stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct
            ),
            stored_warmup_bars=stored_warmup,
        )
    except BacktestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # Added here rather than inside run_backtest, which is handed bars and has
    # no idea what range was asked for. The provider caps history by interval
    # and returns what it has without comment, so without this the result
    # displays the requested range and a strategy looks validated over years
    # it was never shown.
    shortfall = truncation_note(payload.start, payload.end, result.first_bar_at, result.last_bar_at)
    if shortfall:
        result.notes.insert(0, shortfall)

    return _persist(
        db, user, payload, strategy, source_code, symbol, timeframe, data_source, result
    )


@router.get("", response_model=list[BacktestRunRead])
def list_backtests(
    strategy_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[BacktestRun]:
    query = db.query(BacktestRun).filter(BacktestRun.user_id == user.id)
    if strategy_id is not None:
        query = query.filter(BacktestRun.strategy_id == strategy_id)
    return query.order_by(BacktestRun.id.desc()).offset(offset).limit(limit).all()


@router.get("/{run_id}", response_model=BacktestRunDetail)
def get_backtest(
    run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> BacktestRun:
    run = (
        db.query(BacktestRun)
        .filter(BacktestRun.id == run_id, BacktestRun.user_id == user.id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backtest run not found")
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backtest(
    run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    """Throw one run away.

    The list keeps only the most recent thirty and evicts silently, so without
    this the run worth keeping as a baseline is the one that disappears while
    thirty parameter experiments stay. Nothing depends on a run -- it is a
    snapshot, and it already survives its strategy being deleted -- so this is
    a plain delete.
    """
    run = (
        db.query(BacktestRun)
        .filter(BacktestRun.id == run_id, BacktestRun.user_id == user.id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backtest run not found")
    db.delete(run)
    db.commit()


@router.delete("")
def clear_backtests(
    strategy_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict[str, int]:
    query = db.query(BacktestRun).filter(BacktestRun.user_id == user.id)
    if strategy_id is not None:
        query = query.filter(BacktestRun.strategy_id == strategy_id)
    deleted = query.delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}
