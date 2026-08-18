from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.strategy import Strategy
from app.models.user import User
from app.schemas.strategy import (
    SampleStrategyInfo,
    StrategyCreate,
    StrategyDetail,
    StrategyRead,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidateResult,
)
from app.services.strategy_runtime import StrategyValidationError, code_hash, compile_strategy

router = APIRouter(prefix="/strategies", tags=["strategies"])

_SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "strategies_storage" / "samples"

# Prices chosen to exercise both a warm-up ("not enough data yet") period and
# an actual crossover, so /validate gives useful feedback on real strategies.
_DEFAULT_SAMPLE_PRICES = [100, 101, 99, 102, 103, 105, 104, 108, 110, 107]


def _validate(source_code: str, sample_prices: list[float] | None = None) -> StrategyValidateResult:
    try:
        loaded = compile_strategy(source_code)
    except StrategyValidationError as exc:
        return StrategyValidateResult(ok=False, error=str(exc))

    prices = sample_prices or _DEFAULT_SAMPLE_PRICES
    try:
        signals = [loaded.on_tick(float(p)) for p in prices]
    except Exception as exc:
        return StrategyValidateResult(
            ok=False,
            error=f"Strategy compiled but on_tick() raised: {exc}",
            detected_name=loaded.name,
            detected_symbol=loaded.symbol,
        )

    return StrategyValidateResult(
        ok=True,
        detected_name=loaded.name,
        detected_symbol=loaded.symbol,
        sample_signals=signals,
    )


def _get_owned_strategy(db: Session, user: User, strategy_id: int) -> Strategy:
    strategy = (
        db.query(Strategy)
        .filter(Strategy.id == strategy_id, Strategy.user_id == user.id)
        .first()
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


@router.get("/samples", response_model=list[SampleStrategyInfo])
def list_samples(user: User = Depends(get_current_active_user)) -> list[SampleStrategyInfo]:
    samples = []
    for path in sorted(_SAMPLES_DIR.glob("*.py")):
        samples.append(SampleStrategyInfo(filename=path.name, source_code=path.read_text()))
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
    db.delete(strategy)
    db.commit()


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
    return strategy
