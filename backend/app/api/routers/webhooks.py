import hmac
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.enums import OrderSide, OrderSource
from app.models.strategy import Strategy
from app.models.user import User
from app.models.webhook import TradingViewWebhookLog
from app.schemas.webhook import TradingViewAlert
from app.services.signals import SignalIn, create_pending_order

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_MAX_LOGGED_BODY_BYTES = 8 * 1024


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


def _reject_with_log(db: Session, log: TradingViewWebhookLog, error: str) -> JSONResponse:
    log.error = error
    db.add(log)
    db.commit()
    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": False, "error": error})


@router.post("/tradingview", status_code=status.HTTP_202_ACCEPTED)
async def tradingview_webhook(request: Request, db: Session = Depends(get_db)):
    """Public endpoint, secured by a shared secret carried in the JSON body
    (not a header: TradingView alert webhooks can't send custom headers,
    and the body often arrives as text/plain). Always writes an audit row.

    Returns 202 for a genuinely accepted signal. TradingView retries any
    non-2xx response, so failures that would never succeed on retry
    (malformed JSON, an invalid payload shape, no user to attribute it to)
    return 200 with the error logged instead of a 4xx/5xx."""
    raw_body = await request.body()
    log = TradingViewWebhookLog(
        raw_body=raw_body[:_MAX_LOGGED_BODY_BYTES].decode("utf-8", errors="replace"),
        remote_ip=request.client.host if request.client else None,
    )

    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        log.parsed_ok = False
        return _reject_with_log(db, log, f"invalid JSON: {exc}")

    secret = str(payload.get("secret", ""))
    if not hmac.compare_digest(secret, settings.TV_WEBHOOK_SECRET):
        log.parsed_ok = True
        log.signature_valid = False
        log.error = "invalid secret"
        db.add(log)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    log.signature_valid = True

    try:
        alert = TradingViewAlert.model_validate(payload)
    except ValidationError as exc:
        log.parsed_ok = False
        return _reject_with_log(db, log, f"invalid payload: {exc}")

    log.parsed_ok = True
    symbol = alert.symbol.upper()
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
            raw_payload=payload,
        ),
    )

    if result.order is not None:
        log.order_id = result.order.id
    db.add(log)
    db.commit()

    return {"ok": True, "created": result.created, "reason": result.reason}
