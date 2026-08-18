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
    StrategyGenerateRequest,
    StrategyGenerateResult,
    StrategyRead,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidateResult,
)
from app.services.ai_provider import get_ai_provider
from app.services.strategy_generator import (
    build_repair_prompt,
    build_request_prompt,
    build_system_prompt,
    extract_code,
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
        sample_signals=validation.sample_signals,
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
    request_prompt = build_request_prompt(payload.description, payload.symbol)

    first = provider.ask(request_prompt, system=system_prompt)
    if not first.ok:
        return StrategyGenerateResult(ok=False, error=first.error)

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
