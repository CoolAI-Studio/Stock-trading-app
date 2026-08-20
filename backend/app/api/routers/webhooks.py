import hmac
import json
import logging
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.config import settings
from app.db.session import get_db
from app.models.enums import OrderSide, OrderSource
from app.models.mixins import utcnow
from app.models.strategy import Strategy
from app.models.user import User
from app.models.webhook import TradingViewWebhookLog
from app.schemas.webhook import (
    TradingViewAlert,
    TradingViewSetup,
    TradingViewWebhookLogRead,
)
from app.services import symbol_search
from app.services.signals import SignalIn, create_pending_order

logger = logging.getLogger("app.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# A TradingView alert message is a few hundred bytes. 64KB is already absurd
# for one, so anything past it is refused without being read to the end --
# the whole point of the limit is to not spend memory or a database row on
# something no legitimate caller sends.
_MAX_BODY_BYTES = 64 * 1024

# How much of an accepted payload is kept. This is an audit trail, not a
# replay log.
_MAX_LOGGED_BODY_CHARS = 8 * 1024

# Retention for the audit table. Two bounds, because they fail differently:
# the age bound keeps old alerts from lingering but does nothing about a burst
# inside the window, and the row bound is the one that actually caps disk.
# Neon's free tier is 0.5GB for the entire database, and a database with no
# space left fails *writes* -- orders, positions, the worker, all of it. An
# audit trail is not worth taking the app down for.
_LOG_RETENTION_DAYS = 30
_LOG_MAX_ROWS = 500


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _read_bounded_body(request: Request) -> bytes | None:
    """The request body, or None if the client sent more than the limit.

    Streamed rather than `await request.body()` so an oversized body is
    abandoned partway through instead of being buffered in full first. The
    Content-Length header is checked too, but only as a shortcut: a chunked
    request declares no length at all, so the arriving bytes are what has to
    be counted.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        return None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _resolve_user(db: Session, symbol: str, strategy_name: str | None) -> User | None:
    """Attributes an inbound alert to a user. There's no per-user webhook
    secret in v1 (TV_WEBHOOK_SECRET is one shared value), so this is really
    about a single-owner deployment -- prefer a strategy that matches the
    alert's symbol (and name, if given) for forward-compatibility, falling
    back to the only user in the DB."""
    query = db.query(Strategy).filter(Strategy.symbol == symbol)
    if strategy_name:
        by_name = query.filter(Strategy.name == strategy_name).first()
        if by_name is not None:
            return db.get(User, by_name.user_id)
    by_symbol = query.first()
    if by_symbol is not None:
        return db.get(User, by_symbol.user_id)
    return db.query(User).order_by(User.id).first()


def _prune_audit_log(db: Session) -> None:
    """Enforces the two retention bounds described at _LOG_MAX_ROWS.

    Only the authenticated path reaches this, so it runs at TradingView alert
    volume -- a handful a day -- not at whatever rate a stranger can generate.
    """
    cutoff = utcnow() - timedelta(days=_LOG_RETENTION_DAYS)
    db.query(TradingViewWebhookLog).filter(TradingViewWebhookLog.received_at < cutoff).delete(
        synchronize_session=False
    )

    # Deleted by id rather than with DELETE ... LIMIT, which SQLite and
    # Postgres disagree about: "older than the Nth newest id" is plain SQL on
    # both, and ids are monotonic here.
    oldest_kept = (
        db.query(TradingViewWebhookLog.id)
        .order_by(TradingViewWebhookLog.id.desc())
        .offset(_LOG_MAX_ROWS - 1)
        .first()
    )
    if oldest_kept is not None:
        db.query(TradingViewWebhookLog).filter(TradingViewWebhookLog.id < oldest_kept[0]).delete(
            synchronize_session=False
        )
    db.commit()


def _seen_recently(db: Session, raw_body: str) -> bool:
    """Whether this exact body already arrived inside the replay window.

    Compared on the stored, secret-stripped body, so it is the alert's content
    that is matched rather than the credential wrapping it. A price that moved
    makes a different body and gets through, which is what keeps this from
    swallowing real signals.
    """
    cutoff = utcnow() - timedelta(seconds=settings.TV_WEBHOOK_REPLAY_WINDOW_SEC)
    return (
        db.query(TradingViewWebhookLog)
        .filter(
            TradingViewWebhookLog.raw_body == raw_body,
            TradingViewWebhookLog.signature_valid.is_(True),
            TradingViewWebhookLog.received_at >= cutoff,
            TradingViewWebhookLog.id != None,  # noqa: E711 -- exclude the unsaved row
        )
        .first()
        is not None
    )


def _persist_audit(db: Session, log: TradingViewWebhookLog) -> None:
    db.add(log)
    db.commit()
    _prune_audit_log(db)


def _reject_with_log(db: Session, log: TradingViewWebhookLog, error: str) -> JSONResponse:
    log.error = error
    _persist_audit(db, log)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": False, "error": error})


@router.post("/tradingview", status_code=status.HTTP_202_ACCEPTED)
async def tradingview_webhook(request: Request, db: Session = Depends(get_db)):
    """Public endpoint, secured by a shared secret carried in the JSON body
    (not a header: TradingView alert webhooks can't send custom headers,
    and the body often arrives as text/plain).

    Nothing is written to the database until that secret checks out. The path
    is public and guessable, nothing else authenticates the caller, and no
    cleanup existed for the audit table -- so an audit row written before the
    check meant anyone who found the URL could append storage in a loop until
    Neon's 0.5GB free tier was full, and a database with no space left fails
    every write the app makes. Rejected requests are reported to the
    application log instead, which the hosting platform already rotates.

    Returns 202 for a genuinely accepted signal. TradingView retries any
    non-2xx response, so failures that would never succeed on retry
    (malformed JSON, an invalid payload shape, no user to attribute it to)
    return 200 with the error logged instead of a 4xx/5xx."""
    raw_body = await _read_bounded_body(request)
    if raw_body is None:
        logger.warning(
            "tradingview webhook: refused a body over %d bytes from %s",
            _MAX_BODY_BYTES,
            _client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="payload too large"
        )

    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        # The secret travels inside the JSON, so a body that will not parse is
        # a body that cannot be authenticated. No row.
        logger.warning("tradingview webhook: unparseable body from %s", _client_ip(request))
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"ok": False, "error": f"invalid JSON: {exc}"},
        )

    secret = str(payload.get("secret", ""))
    if not hmac.compare_digest(secret, settings.TV_WEBHOOK_SECRET):
        logger.warning("tradingview webhook: invalid secret from %s", _client_ip(request))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    # Authenticated from here, so audit rows are worth writing: their volume is
    # bounded by whoever holds the secret, and _prune_audit_log caps them even
    # if that assumption ever stops holding.
    #
    # Stored re-serialized without the secret rather than as the bytes that
    # arrived: the secret is a bearer credential, and an audit row quoting it
    # back would be a second, unencrypted copy of the password guarding this
    # endpoint. Everything with audit value -- symbol, action, quantity, the
    # alert id -- is kept.
    audited = {key: value for key, value in payload.items() if key != "secret"}
    log = TradingViewWebhookLog(
        raw_body=json.dumps(audited, ensure_ascii=False)[:_MAX_LOGGED_BODY_CHARS],
        remote_ip=_client_ip(request),
        signature_valid=True,
    )

    try:
        alert = TradingViewAlert.model_validate(payload)
    except ValidationError as exc:
        log.parsed_ok = False
        return _reject_with_log(db, log, f"invalid payload: {exc}")

    log.parsed_ok = True
    log.missing_id = not alert.id

    # An alert with an `id` is exactly idempotent -- create_pending_order's
    # unique key sees to that. One without has no such promise, so the same
    # body arriving twice in a short window is treated as a replay rather than
    # as two decisions. That covers the alerts already configured out there
    # without anybody having to change them.
    #
    # Honest about its limit: a patient attacker replaying an hour apart is
    # not stopped by this, which is why the setup panel pushes `id`.
    if not alert.id and _seen_recently(db, log.raw_body):
        return _reject_with_log(
            db, log, "重複的警報內容（短時間內收到一模一樣的訊息），已當成重放略過"
        )

    # TradingView's {{ticker}} sends 「2330」, never 「2330.TW」 -- and Yahoo
    # answers a bare 2330 with an unrelated Japanese company, so this used to
    # create an order that priced the wrong stock with complete confidence.
    # There is nobody present to pick, so it is resolved from our own registry
    # (a lookup with a unique answer, unlike Yahoo's cross-market search) and
    # the adjustment is recorded. Anything without a unique answer is refused.
    symbol, adjustment = symbol_search.resolve_incoming(alert.symbol, alert.exchange)
    if symbol is None:
        if adjustment and adjustment.startswith("__unsupported__"):
            market = adjustment.removeprefix("__unsupported__")
            return _reject_with_log(
                db,
                log,
                f"這則警報來自 {market} 市場，這個 app 只支援台股、美股與 Binance。"
                f"沒有建立訂單 —— 「{alert.symbol}」這個代號在台股也存在，"
                "硬對應過去會盯到完全不同的一家公司。",
            )
        return _reject_with_log(
            db,
            log,
            f"看不懂這個代號「{alert.symbol}」。台股請送 2330.TW 這種格式"
            "（或四碼代號，會自動對應），美股直接送代號即可。",
        )
    log.note = adjustment

    user = _resolve_user(db, symbol, alert.strategy)
    if user is None:
        return _reject_with_log(db, log, "no user configured to receive this alert")

    log.user_id = user.id

    result = create_pending_order(
        db,
        user,
        SignalIn(
            symbol=symbol,
            side=OrderSide.BUY if alert.action == "buy" else OrderSide.SELL,
            source=OrderSource.TRADINGVIEW,
            quantity=alert.quantity or Decimal(1),
            signal_price=alert.price,
            idempotency_key=alert.id,
            raw_payload=audited,
        ),
    )

    if result.order is not None:
        log.order_id = result.order.id
    _persist_audit(db, log)

    return {"ok": True, "created": result.created, "reason": result.reason}


@router.get("/tradingview/logs", response_model=list[TradingViewWebhookLogRead])
def list_webhook_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
) -> list[TradingViewWebhookLog]:
    """What TradingView actually sent.

    These rows have been written on every authenticated call since the webhook
    existed, and pruned on a schedule -- created and then deleted without
    anybody ever having been able to read them. When an alert did not become
    an order, the owner had no way to tell whether it arrived at all, whether
    the secret was wrong, whether the JSON was malformed, or whether a risk
    gate refused it.

    Not filtered by user: a call that failed the secret or failed to parse has
    no user attached, and those are exactly the rows somebody is looking for.
    This deployment has one owner (see CLAUDE.md), so there is nobody else's
    traffic to leak; the day that changes, this needs revisiting along with
    the webhook's own single-tenant user resolution.
    """
    return (
        db.query(TradingViewWebhookLog)
        .order_by(TradingViewWebhookLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/tradingview/setup", response_model=TradingViewSetup)
def tradingview_setup(_user: User = Depends(get_current_active_user)) -> TradingViewSetup:
    """What to paste into TradingView.

    Nothing told the owner the URL, the field names, or that the message needs
    an `id` -- which is the only thing standing between this endpoint and a
    replay of a captured alert. Served rather than documented, because a URL
    in a docs page is a URL nobody finds.

    The example is a template. Printing the real shared secret into a response
    would put it in every browser cache and every screenshot of this page.
    """
    return TradingViewSetup(
        url=f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/webhooks/tradingview",
        example_message=(
            "{\n"
            '  "secret": "<你的 TV_WEBHOOK_SECRET>",\n'
            '  "symbol": "{{ticker}}",\n'
            '  "exchange": "{{exchange}}",\n'
            '  "action": "buy",\n'
            '  "quantity": 1000,\n'
            '  "price": {{close}},\n'
            '  "id": "{{timenow}}"\n'
            "}"
        ),
        notes=[
            "把上面那段貼進 TradingView 警報的「訊息」欄，網址貼進 Webhook URL。",
            "secret 要換成部署時設定的 TV_WEBHOOK_SECRET，不是這裡顯示的字樣。",
            "id 一定要填。同一個 id 只會建立一次訂單——沒有它，任何人只要重送一次"
            "抄到的訊息就能重複下單。用 {{timenow}} 最省事。",
            # The old wording printed the {{ticker}} template and then said TW
            # must look like 2330.TW -- an instruction that contradicts itself,
            # because that placeholder never includes the exchange. It now says
            # what actually happens.
            "symbol 用 {{ticker}} 就好。台股圖表送出來的是四碼代號（例如 2330），"
            "系統會自動對應到 2330.TW（上櫃是 .TWO），對應結果會寫在下面的收件紀錄裡；"
            "美股送出來的本來就是正確代號。",
            "找不到對應的代號（打錯、或不是台美股）會被擋下來並記在收件紀錄，"
            "不會建立一筆指向錯誤公司的訂單。",
            # {{exchange}} only arrives if it is in the message, so every alert
            # made before this line existed keeps using the weaker path. Saying
            # so is the difference between a fix and a fix nobody applied.
            "exchange 那一行請務必留著：日股和港股的代號也是四位數，"
            "沒有它就分不出 4502 是武田藥品還是台灣的健信。"
            "已經設定好的舊警報不會自動帶上這一行，請回 TradingView 把訊息補上。",
            "送出後可以在下面的收件紀錄看到它有沒有進來、以及被擋在哪一關。",
        ],
    )
