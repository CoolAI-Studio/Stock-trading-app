from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.enums import DataSource
from app.models.backtest import BacktestRun
from app.models.position import Position
from app.models.strategy import Strategy
from app.models.user import User
from app.schemas.strategy import (
    SampleStrategyInfo,
    StrategyCreate,
    StrategyDetail,
    StrategyFromTemplate,
    StrategyGenerateRequest,
    StrategyGenerateResult,
    StrategyPerformanceRead,
    StrategyRead,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidateResult,
    TemplateFieldRead,
    TemplateRead,
)
from app.services import (
    ai_settings,
    risk_resolver,
    strategy_performance,
    strategy_pool,
    strategy_templates,
    symbol_search,
)
from app.services.ai_provider import get_ai_provider
from app.services.market_data.base import (
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_LABELS,
    Timeframe,
    supports_timeframe,
)
from app.services.market_loop import release_strategy
from app.services.strategy_generator import (
    build_repair_prompt,
    build_request_prompt,
    build_system_prompt,
    extract_code,
    extract_question,
)
from app.services.strategy_runtime import code_hash
from app.services.strategy_worker import (
    StrategyTimedOut,
    StrategyWorkerError,
    WorkerUnavailable,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])

_SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "strategies_storage" / "samples"

# Prices chosen to exercise both a warm-up ("not enough data yet") period and
# an actual crossover, so /validate gives useful feedback on real strategies.
_DEFAULT_SAMPLE_PRICES = [100, 101, 99, 102, 103, 105, 104, 108, 110, 107]

_NO_CODE_ERROR = "AI 沒有回傳任何程式碼，請把策略描述講得更具體一點再試一次。"


def _validate(source_code: str, sample_prices: list[float] | None = None) -> StrategyValidateResult:
    """編譯並試跑一支策略——**在子行程裡**（#18）。

    這個端點只需要一個登入，而它會編譯程式碼、跑 on_tick，然後把回傳值放進 HTTP
    回應。test_sandbox_escape.py 的檔頭把這條路叫做「the worst thing in the app」，
    而且說了真正的答案是獨立行程；那個檔案關掉的是已知的每一條路，不是這件事。

    搬過來還順手補掉一個**沒有任何期限**的洞：`_guarded` 只包 on_tick / on_bar，
    不包建構式，而 compile_strategy 會執行類別主體和 __init__。一支在 __init__ 裡
    `while True` 的策略，會讓這個請求的執行緒永遠回不來。現在期限由父行程用殺行
    程執行，建構式也在裡面。
    """
    prices = [float(price) for price in (sample_prices or _DEFAULT_SAMPLE_PRICES)]
    try:
        answer = strategy_pool.validate(source_code, prices)
    except StrategyTimedOut as exc:
        return StrategyValidateResult(ok=False, error=f"策略跑不完，已經中止：{exc}")
    except WorkerUnavailable as exc:
        # 基礎設施的問題，不是這段程式碼的問題。講清楚是哪一種，不然使用者會去改
        # 一段其實沒有錯的程式碼。
        return StrategyValidateResult(ok=False, error=f"驗證用的行程暫時起不來，請再試一次：{exc}")
    except StrategyWorkerError as exc:
        # 編不起來。StrategyValidationError 的訊息也是從這裡回來的——它在子行程那
        # 端被轉成文字了，因為例外送不過 JSON 管線。
        return StrategyValidateResult(ok=False, error=str(exc))

    detected = {
        "detected_name": answer["name"],
        "detected_symbol": answer["symbol"],
        # Reported here rather than left to the save step. The editor printed
        # 「偵測到：均線（2330）」 in green, which reads as approval; the refusal
        # did happen, later, from a different field, with nothing connecting
        # it to the symbol the code had chosen.
        "symbol_problem": symbol_search.looks_unpriceable(answer["symbol"] or ""),
        # What the form needs to render a field per parameter, with the
        # author's own defaults already in the boxes.
        "declared_params": answer["declared_params"],
        "entry_point": answer["entry_point"],
        # Left out for a tick strategy: it has no candles, and reporting the
        # default would read as a choice the code never made.
        "timeframe": answer["timeframe"] if answer["entry_point"] == "on_bar" else None,
    }

    if "run_error" in answer:
        return StrategyValidateResult(
            ok=False,
            error=f"Strategy compiled but {answer['entry_point']}() raised: {answer['run_error']}",
            **detected,
        )

    return StrategyValidateResult(ok=True, sample_signals=answer["signals"], **detected)


def _check_params(source_code: str, params: dict | None) -> None:
    """Refuse an override the code does not declare, before it is stored.

    Checked here rather than trusted from the form: a stored setting for a
    parameter that no longer exists is a value the owner believes is doing
    something, and the only moment anybody would notice is never.
    """
    if not params:
        return
    try:
        # 子行程裡編（#18）：編譯會執行類別主體和 __init__，所以這也是在跑使用者
        # 的程式碼。錯誤訊息從那一端轉成文字回來——例外送不過 JSON 管線。
        strategy_pool.check_params(source_code, params)
    except StrategyWorkerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


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
        symbol_problem=validation.symbol_problem,
        declared_params=validation.declared_params,
        # Carried through so the editor can say which candle the strategy
        # decided to work in: "周線" was the owner's word, and a strategy that
        # quietly came back daily reads identically in the source box.
        entry_point=validation.entry_point,
        timeframe=validation.timeframe,
        sample_signals=validation.sample_signals,
    )


def _check_timeframe_pair(source_code: str, data_source: DataSource) -> None:
    """The candle a strategy declares must be one its own source actually serves.

    _read_timeframe checks the value against the enum and never sees the
    symbol, so 「self.timeframe = '12h'」 on a US stock compiles happily -- and
    then the market loop fetches nothing for it on every tick, forever. No
    error, no alert, no way to notice. A strategy that silently never runs is
    worse than one that fails loudly, because the owner believes they are being
    watched. 「警告不能停擺」.
    """
    validation = _validate(source_code)
    if validation.timeframe is None:
        return
    # The validator reports the value, not the member -- coerced here so the
    # label lookup and the support check both work on the same type.
    timeframe = Timeframe(validation.timeframe)
    if supports_timeframe(data_source, timeframe):
        return
    offered = "、".join(
        TIMEFRAME_LABELS[option] for option in SUPPORTED_TIMEFRAMES.get(data_source, ())
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"這個策略用的是 {TIMEFRAME_LABELS.get(timeframe, timeframe.value)}，"
            f"但 {data_source.value} 沒有提供這個週期，策略會抓不到 K 棒而完全不動作。"
            f"可以改用：{offered}。"
        ),
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
    payload: StrategyGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> StrategyGenerateResult:
    """Turns a plain-language description into strategy source.

    Validation happens here rather than in the browser so the owner is never
    handed code that POST /strategies would then refuse to save -- the sandbox
    rejects a good deal of what a model writes by default (`import pandas`
    above all), and a rejection the owner cannot act on is worse than nothing.
    """
    provider = get_ai_provider(ai_settings.resolve(db, user.id))
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


@router.get("/templates", response_model=list[TemplateRead])
def list_templates(user: User = Depends(get_current_active_user)) -> list[TemplateRead]:
    """現成的提醒範本，給不寫 Python 的人。

    DECLARED BEFORE /{strategy_id}, and that is not a style choice: the path
    parameter matches any string, so a literal route declared after it is
    unreachable -- 「templates」 would be looked up as a strategy id and 404.
    """
    return [
        TemplateRead(
            key=template.key,
            title=template.title,
            summary=template.summary,
            good_for=template.good_for,
            fields=[TemplateFieldRead(**vars(field)) for field in template.fields],
        )
        for template in strategy_templates.TEMPLATES
    ]


@router.post("/from-template", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
def create_from_template(
    payload: StrategyFromTemplate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Strategy:
    """一則提醒，從表單來，沒有任何一行程式碼經過使用者的手。

    It goes through create_strategy rather than around it. Everything that
    protects a hand-written strategy -- the sandbox, the parameter type check,
    the symbol/feed mismatch check that catches binance + 2330.TW -- protects
    this one too, because it is the same code path with the source filled in
    from a template instead of a text box.

    alert_only is forced on: this is a 提醒系統, and every route into it that
    could quietly produce an order is a route that eventually will.
    """
    template = strategy_templates.get_template(payload.template)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"沒有叫做 {payload.template!r} 的範本。",
        )

    try:
        create = StrategyCreate(
            name=payload.name,
            symbol=payload.symbol,
            data_source=payload.data_source,
            source_code=template.source,
            alert_only=True,
            params=payload.params,
        )
    except ValidationError as exc:
        # Built here rather than parsed from the request body, so FastAPI never
        # sees it -- and an uncaught ValidationError inside a handler is a 500.
        # The person filling in the form gets the reason instead.
        #
        # The MESSAGES, not exc.errors(): that structure carries the original
        # exception object in `ctx`, which is not JSON serialisable, and the
        # sentence the validator wrote (「binance 上沒有 2330.TW」) is the only
        # part anybody can act on anyway.
        reasons = "；".join(
            str(error.get("msg", "")).removeprefix("Value error, ") for error in exc.errors()
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=reasons
        ) from exc

    strategy = create_strategy(create, db=db, user=user)

    # ON, unlike a hand-written strategy. That one starts inactive because its
    # author wants to read it once more before it runs; this one was made by
    # somebody typing a price and pressing a button, and an alert that has to
    # be switched on afterwards is an alarm that does not ring -- with nothing
    # anywhere saying why.
    strategy.is_active = True
    db.commit()
    db.refresh(strategy)
    return strategy


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
    _check_params(payload.source_code, payload.params)
    _check_timeframe_pair(payload.source_code, payload.data_source)

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
        params=payload.params,
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

    # Same for the parameters, and for the same reason: a patch may change the
    # source, or the parameters, or both, and only the merged row says which
    # code the stored overrides will actually be handed to.
    _check_params(strategy.source_code, strategy.params)
    # Against the MERGED row: a patch may change the source, the symbol, the
    # source, or any pair of them, and only the merged result says which candle
    # will be asked of which provider.
    _check_timeframe_pair(strategy.source_code, strategy.data_source)

    # Checked on the MERGED row, not on the payload. A patch that changes only
    # the symbol, or only the data source, cannot be judged by the schema --
    # the other half is on the stored row -- and either edit alone can produce
    # a pairing that never prices and is polled around the clock.
    mismatch = symbol_search.market_mismatch(strategy.symbol, strategy.data_source)
    if mismatch:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=mismatch)

    # A source edit already recompiles -- the registry keys on a content hash.
    # Editing the symbol does not, and the instance's accumulated prices
    # belong to the old symbol, so they would seed the new one's average.
    if "symbol" in data or "source_code" in data or "params" in data:
        # params too: the registry keys on them, but releasing here is what
        # makes the change take effect on the very next tick rather than
        # whenever something else happened to invalidate the entry.
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


@router.get("/{strategy_id}/performance", response_model=StrategyPerformanceRead)
def strategy_performance_report(
    strategy_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """What this strategy has actually done since going live.

    The backtest produces a full report and a month of real running produced
    nothing -- the orders page says 策略訊號 without saying which strategy, so
    two running at once were indistinguishable.
    """
    strategy = _get_owned_strategy(db, user, strategy_id)
    return strategy_performance.summarise(db, strategy)
