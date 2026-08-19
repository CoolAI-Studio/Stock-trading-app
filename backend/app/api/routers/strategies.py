from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.backtest import BacktestRun
from app.models.position import Position
from app.models.strategy import Strategy
from app.models.user import User
from app.schemas.strategy import (
    SampleStrategyInfo,
    StrategyCreate,
    StrategyDetail,
    StrategyGenerateRequest,
    StrategyGenerateResult,
    StrategyRead,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidateResult,
)
from app.services import risk_resolver
from app.services.ai_provider import get_ai_provider
from app.services.market_data.base import bars_from_closes
from app.services.market_loop import release_strategy
from app.services.strategy_generator import (
    build_repair_prompt,
    build_request_prompt,
    build_system_prompt,
    extract_code,
    extract_question,
)
from app.services.strategy_runtime import StrategyValidationError, code_hash, compile_strategy

router = APIRouter(prefix="/strategies", tags=["strategies"])

_SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "strategies_storage" / "samples"

# Prices chosen to exercise both a warm-up ("not enough data yet") period and
# an actual crossover, so /validate gives useful feedback on real strategies.
_DEFAULT_SAMPLE_PRICES = [100, 101, 99, 102, 103, 105, 104, 108, 110, 107]

_NO_CODE_ERROR = "AI 沒有回傳任何程式碼，請把策略描述講得更具體一點再試一次。"


def _validate(source_code: str, sample_prices: list[float] | None = None) -> StrategyValidateResult:
    try:
        loaded = compile_strategy(source_code)
    except StrategyValidationError as exc:
        return StrategyValidateResult(ok=False, error=str(exc))

    detected = {
        "detected_name": loaded.name,
        "detected_symbol": loaded.symbol,
        "entry_point": loaded.entry_point,
        # Left out for a tick strategy: it has no candles, and reporting the
        # default would read as a choice the code never made.
        "timeframe": loaded.timeframe.value if loaded.entry_point == "on_bar" else None,
    }

    prices = [float(p) for p in (sample_prices or _DEFAULT_SAMPLE_PRICES)]
    try:
        if loaded.entry_point == "on_bar":
            # The same sample prices, turned into candles closing at them, so
            # the dry run is comparable whichever entry point the code uses.
            bars = bars_from_closes(loaded.symbol, loaded.timeframe, prices)
            signals = [loaded.on_bar(bar) for bar in bars]
        else:
            signals = [loaded.on_tick(price) for price in prices]
    except Exception as exc:
        return StrategyValidateResult(
            ok=False,
            error=f"Strategy compiled but {loaded.entry_point}() raised: {exc}",
            **detected,
        )

    return StrategyValidateResult(ok=True, sample_signals=signals, **detected)


def _to_generate_result(
    source_code: str, validation: StrategyValidateResult, repair_note: str | None = None
) -> StrategyGenerateResult:
    """The code is returned even when it failed to validate: the owner can read
    and fix it, which beats being told only that something went wrong. The
    validator speaks English, so the reason it is being shown at all gets a
    Traditional Chinese lead-in."""
    error = validation.error
    if error is not None:
        error = f"AI 產生的程式碼無法通過驗證：{error}"
        if repair_note:
            error = f"{error}（自動修正未能進行：{repair_note}）"

    return StrategyGenerateResult(
        ok=validation.ok,
        error=error,
        source_code=source_code,
        detected_name=validation.detected_name,
        detected_symbol=validation.detected_symbol,
        # Carried through so the editor can say which candle the strategy
        # decided to work in: "周線" was the owner's word, and a strategy that
        # quietly came back daily reads identically in the source box.
        entry_point=validation.entry_point,
        timeframe=validation.timeframe,
        sample_signals=validation.sample_signals,
    )


def _get_owned_strategy(db: Session, user: User, strategy_id: int) -> Strategy:
    strategy = (
        db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user.id).first()
    )
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return strategy


@router.get("", response_model=list[StrategyRead])
def list_strategies(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> list[Strategy]:
    return db.query(Strategy).filter(Strategy.user_id == user.id).order_by(Strategy.id).all()


@router.post("/validate", response_model=StrategyValidateResult)
def validate_strategy(
    payload: StrategyValidateRequest, user: User = Depends(get_current_active_user)
) -> StrategyValidateResult:
    return _validate(payload.source_code, payload.sample_prices)


@router.post("/generate", response_model=StrategyGenerateResult)
def generate_strategy(
    payload: StrategyGenerateRequest, user: User = Depends(get_current_active_user)
) -> StrategyGenerateResult:
    """Turns a plain-language description into strategy source.

    Validation happens here rather than in the browser so the owner is never
    handed code that POST /strategies would then refuse to save -- the sandbox
    rejects a good deal of what a model writes by default (`import pandas`
    above all), and a rejection the owner cannot act on is worse than nothing.
    """
    provider = get_ai_provider()
    system_prompt = build_system_prompt()
    request_prompt = build_request_prompt(
        payload.description, payload.symbol, payload.question, payload.answer
    )

    first = provider.ask(request_prompt, system=system_prompt)
    if not first.ok:
        return StrategyGenerateResult(ok=False, error=first.error)

    # Checked before the code, and only on this first round: a question is not
    # something the repair round can fix, and by then the model has committed
    # to source the owner can still read and correct. `error` stays None --
    # being asked something is not a failure.
    question = extract_question(first.reply)
    if question:
        return StrategyGenerateResult(ok=False, question=question)

    source_code = extract_code(first.reply)
    if not source_code:
        return StrategyGenerateResult(ok=False, error=_NO_CODE_ERROR)

    validation = _validate(source_code)
    if validation.ok:
        return _to_generate_result(source_code, validation)

    # Exactly one repair round. Every call spends from a small free-tier daily
    # allowance, and a model that has already been handed the contract and the
    # precise error is not going to find it on a third try.
    repair = provider.ask(
        build_repair_prompt(request_prompt, source_code, validation.error),
        system=system_prompt,
    )
    if not repair.ok:
        return _to_generate_result(source_code, validation, repair_note=repair.error)

    repaired = extract_code(repair.reply)
    if not repaired:
        return _to_generate_result(source_code, validation)
    return _to_generate_result(repaired, _validate(repaired))


@router.get("/samples", response_model=list[SampleStrategyInfo])
def list_samples(user: User = Depends(get_current_active_user)) -> list[SampleStrategyInfo]:
    samples = []
    for path in sorted(_SAMPLES_DIR.glob("*.py")):
        # encoding pinned, not left to the platform default: the samples
        # carry Traditional Chinese comments, and read_text() on a Windows
        # machine defaults to cp950 and raises. It happens to work on the
        # Linux container, so this would have been a bug only the owner's own
        # laptop ever saw.
        samples.append(
            SampleStrategyInfo(filename=path.name, source_code=path.read_text(encoding="utf-8"))
        )
    return samples


@router.post("", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Strategy:
    validation = _validate(payload.source_code)
    if not validation.ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=validation.error
        )

    strategy = Strategy(
        user_id=user.id,
        name=payload.name,
        symbol=payload.symbol,
        data_source=payload.data_source,
        source_code=payload.source_code,
        code_hash=code_hash(payload.source_code),
        default_quantity=payload.default_quantity,
        warmup_bars=payload.warmup_bars,
        alert_only=payload.alert_only,
        # Straight from the shared list so a knob added to the overrides can
        # never be accepted by the schema and then dropped on the floor here.
        **{field: getattr(payload, field) for field in risk_resolver.OVERRIDABLE_FIELDS},
    )
    db.add(strategy)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A strategy with this name already exists"
        ) from exc
    db.refresh(strategy)
    return strategy


@router.get("/{strategy_id}", response_model=StrategyDetail)
def get_strategy(
    strategy_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> Strategy:
    return _get_owned_strategy(db, user, strategy_id)


@router.patch("/{strategy_id}", response_model=StrategyRead)
def update_strategy(
    strategy_id: int,
    payload: StrategyUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Strategy:
    strategy = _get_owned_strategy(db, user, strategy_id)
    data = payload.model_dump(exclude_unset=True)

    if "source_code" in data:
        validation = _validate(data["source_code"])
        if not validation.ok:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=validation.error
            )
        data["code_hash"] = code_hash(data["source_code"])

    for field, value in data.items():
        setattr(strategy, field, value)

    # A source edit already recompiles -- the registry keys on a content hash.
    # Editing the symbol does not, and the instance's accumulated prices
    # belong to the old symbol, so they would seed the new one's average.
    if "symbol" in data or "source_code" in data:
        release_strategy(strategy.id)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A strategy with this name already exists"
        ) from exc
    db.refresh(strategy)
    return strategy


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_strategy(
    strategy_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    strategy = _get_owned_strategy(db, user, strategy_id)
    # Release the positions this strategy owned before it goes. The column
    # declares ondelete="SET NULL", but SQLite only enforces a foreign key
    # when PRAGMA foreign_keys is on and nothing here turns it on, so the
    # constraint alone would leave it pointing at a row that no longer exists.
    # The exit scan resolves that dangling id to None and reverts to the
    # global stop-loss, while the positions page goes on badging the position
    # with the dead strategy -- the gate and the display disagreeing is the
    # one outcome the attribution column exists to prevent. Done explicitly
    # rather than by constraint so it holds on Postgres too.
    db.query(Position).filter(Position.strategy_id == strategy.id).update(
        {Position.strategy_id: None}, synchronize_session=False
    )
    # Backtest runs are released the same way and for the same reason. They
    # deliberately survive their strategy -- each one snapshots the source it
    # scored, so it stays readable -- but a run left pointing at a deleted id
    # would be re-attributed the moment SQLite handed that id to a new row.
    db.query(BacktestRun).filter(BacktestRun.strategy_id == strategy.id).update(
        {BacktestRun.strategy_id: None}, synchronize_session=False
    )
    db.delete(strategy)
    db.commit()
    # Nothing can reach this instance again, and the worker runs in a single
    # long-lived process, so left in the cache it is simply a leak.
    release_strategy(strategy_id)


@router.post("/{strategy_id}/activate", response_model=StrategyRead)
def activate_strategy(
    strategy_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> Strategy:
    strategy = _get_owned_strategy(db, user, strategy_id)
    strategy.is_active = True
    strategy.consecutive_errors = 0
    strategy.last_error = None
    db.commit()
    db.refresh(strategy)
    return strategy


@router.post("/{strategy_id}/deactivate", response_model=StrategyRead)
def deactivate_strategy(
    strategy_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> Strategy:
    strategy = _get_owned_strategy(db, user, strategy_id)
    strategy.is_active = False
    db.commit()
    db.refresh(strategy)
    # Pausing has to throw the accumulated state away: on resume the price
    # series would otherwise run straight from the prices it had before the
    # pause into today's, with the gap invisible to the strategy.
    release_strategy(strategy_id)
    return strategy
