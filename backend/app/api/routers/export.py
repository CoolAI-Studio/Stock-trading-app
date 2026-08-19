"""CSV downloads of the owner's own records.

Every May there is a tax return, and at some point an accountant wants the
year's fills. There was no way to produce that except reading the screen a
page at a time -- and the history page showed only the most recent fifty rows
anyway.

Two details that decide whether the file is usable:

- **A UTF-8 BOM.** Excel on a Traditional Chinese Windows reads a BOM-less
  file as the system codepage, so every Chinese header arrives as mojibake and
  the app looks like it produced a broken file.
- **Headers in the owner's language.** The person opening this is doing their
  tax return, not reading a schema.
"""

import csv
import io
from collections.abc import Callable, Iterable

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.enums import OrderSide, OrderStatus
from app.models.order import Order
from app.models.position import Position
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User

router = APIRouter(prefix="/export", tags=["export"])

_SIDE = {OrderSide.BUY: "買進", OrderSide.SELL: "賣出"}
_STATUS = {
    OrderStatus.PENDING: "待確認",
    OrderStatus.CONFIRMED: "已成交",
    OrderStatus.REJECTED: "已拒絕",
    OrderStatus.EXPIRED: "已過期",
    OrderStatus.FAILED: "失敗",
}


def _stamp(value) -> str:
    """Local-looking ISO without the offset noise, which is what a spreadsheet
    sorts correctly and a person can read."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _orders(db: Session, user: User) -> tuple[list[str], Iterable[list]]:
    header = [
        "訂單編號",
        "建立時間",
        "成交時間",
        "代號",
        "買賣別",
        "委託數量",
        "成交數量",
        "成交價",
        "狀態",
        "來源",
        "策略編號",
    ]
    rows = db.query(Order).filter(Order.user_id == user.id).order_by(Order.id).all()
    return header, (
        [
            order.id,
            _stamp(order.created_at),
            _stamp(order.filled_at),
            order.symbol,
            _SIDE.get(order.side, order.side.value),
            order.quantity,
            # What actually reached the position, which is the number the tax
            # figure comes from. Falls back for rows written before partial
            # fills were recorded.
            order.filled_quantity if order.filled_quantity is not None else "",
            order.fill_price if order.fill_price is not None else "",
            _STATUS.get(order.status, order.status.value),
            order.source.value,
            order.strategy_id or "",
        ]
        for order in rows
    )


def _positions(db: Session, user: User) -> tuple[list[str], Iterable[list]]:
    header = ["代號", "數量", "平均成本", "已實現損益", "建倉時間", "策略編號"]
    rows = db.query(Position).filter(Position.user_id == user.id).order_by(Position.symbol).all()
    return header, (
        [
            position.symbol,
            position.quantity,
            position.avg_entry_price,
            position.realized_pnl,
            _stamp(position.opened_at),
            position.strategy_id or "",
        ]
        for position in rows
    )


def _alerts(db: Session, user: User) -> tuple[list[str], Iterable[list]]:
    header = ["時間", "策略", "代號", "買賣別", "價格", "通知結果"]
    names = {
        strategy.id: strategy.name
        for strategy in db.query(Strategy).filter(Strategy.user_id == user.id).all()
    }
    rows = (
        db.query(StrategyAlert)
        .filter(StrategyAlert.user_id == user.id)
        .order_by(StrategyAlert.id)
        .all()
    )
    return header, (
        [
            _stamp(alert.created_at),
            names.get(alert.strategy_id, alert.strategy_id),
            alert.symbol,
            _SIDE.get(alert.side, alert.side.value),
            alert.price,
            "已送出" if alert.status.value == "sent" else "未送達",
        ]
        for alert in rows
    )


_EXPORTS: dict[str, Callable[[Session, User], tuple[list[str], Iterable[list]]]] = {
    "orders": _orders,
    "positions": _positions,
    "alerts": _alerts,
}


@router.get("/{resource}.csv")
def export_csv(
    resource: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Response:
    builder = _EXPORTS.get(resource)
    if builder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"沒有這種匯出：{resource}。可用的有 {', '.join(sorted(_EXPORTS))}。",
        )

    header, rows = builder(db, user)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # Written even when there are no rows: an empty file is confusing, while
    # headers with nothing under them say plainly that there is nothing yet.
    writer.writerow(header)
    writer.writerows(rows)

    return Response(
        # utf-8-sig, not utf-8: Excel on a Traditional Chinese Windows reads a
        # BOM-less file as the system codepage and turns every Chinese header
        # into mojibake.
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resource}.csv"'},
    )
