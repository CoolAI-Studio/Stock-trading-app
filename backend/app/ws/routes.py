import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.enums import OrderStatus
from app.models.market import MarketQuote
from app.models.order import Order
from app.models.position import Position
from app.ws.manager import manager
from app.ws.tickets import redeem_ticket

router = APIRouter(tags=["ws"])

logger = logging.getLogger("app.ws.routes")

_HEARTBEAT_INTERVAL_SEC = 20
_TICKET_INVALID_CLOSE_CODE = 4401  # mirrors HTTP 401; 4000-4999 is the app-defined range


def _initial_snapshot(db: Session, user_id: int) -> dict:
    """Open positions + pending orders + their latest quotes, sent right
    after connect so the client isn't blank until the next tick."""
    positions = db.query(Position).filter(Position.user_id == user_id, Position.quantity != 0).all()
    pending_orders = (
        db.query(Order).filter(Order.user_id == user_id, Order.status == OrderStatus.PENDING).all()
    )
    symbols = {p.symbol for p in positions} | {o.symbol for o in pending_orders}
    quotes = db.query(MarketQuote).filter(MarketQuote.symbol.in_(symbols)).all() if symbols else []

    return {
        "positions": [
            {
                "symbol": p.symbol,
                "quantity": str(p.quantity),
                "avg_entry_price": str(p.avg_entry_price),
            }
            for p in positions
        ],
        "pending_orders": [
            {"id": o.id, "symbol": o.symbol, "side": o.side.value, "quantity": str(o.quantity)}
            for o in pending_orders
        ],
        "quotes": [{"symbol": q.symbol, "price": str(q.price)} for q in quotes],
    }


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, ticket: str, db: Session = Depends(get_db)) -> None:
    user_id = redeem_ticket(ticket)
    if user_id is None:
        await websocket.close(code=_TICKET_INVALID_CLOSE_CODE)
        return

    await websocket.accept()
    await manager.connect(user_id, websocket)

    await websocket.send_json({"type": "snapshot", "v": 1, "data": _initial_snapshot(db, user_id)})

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=_HEARTBEAT_INTERVAL_SEC)
            except TimeoutError:
                await websocket.send_json({"type": "heartbeat", "v": 1, "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, websocket)
