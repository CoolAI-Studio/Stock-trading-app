from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.position import Position
from app.models.user import User
from app.schemas.position import PositionAdjust, PositionRead

router = APIRouter(prefix="/positions", tags=["positions"])


def _get_owned_position(db: Session, user: User, symbol: str) -> Position:
    position = (
        db.query(Position)
        .filter(Position.user_id == user.id, Position.symbol == symbol.upper())
        .first()
    )
    if position is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No position for this symbol"
        )
    return position


@router.get("", response_model=list[PositionRead])
def list_positions(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> list[Position]:
    return (
        db.query(Position)
        .filter(Position.user_id == user.id, Position.quantity != 0)
        .order_by(Position.symbol)
        .all()
    )


@router.get("/{symbol}", response_model=PositionRead)
def get_position(
    symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> Position:
    return _get_owned_position(db, user, symbol)


@router.patch("/{symbol}", response_model=PositionRead)
def adjust_position(
    symbol: str,
    payload: PositionAdjust,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Position:
    position = (
        db.query(Position)
        .filter(Position.user_id == user.id, Position.symbol == symbol.upper())
        .first()
    )
    if position is None:
        position = Position(user_id=user.id, symbol=symbol.upper())
        db.add(position)

    position.quantity = payload.quantity
    position.avg_entry_price = payload.avg_entry_price
    db.commit()
    db.refresh(position)
    return position


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def flatten_position(
    symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    position = _get_owned_position(db, user, symbol)
    position.quantity = Decimal(0)
    position.avg_entry_price = Decimal(0)
    db.commit()
