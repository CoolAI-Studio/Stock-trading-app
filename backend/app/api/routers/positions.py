from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.market import MarketQuote
from app.models.position import Position
from app.models.user import User
from app.schemas.position import PositionAdjust, PositionRead
from app.services import symbol_search

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


def _valued(db: Session, positions: list[Position]) -> list[PositionRead]:
    """Attach the live quote to each position.

    The prices are already here -- the worker upserts market_quotes on every
    poll and the stop-loss scan reads the same rows on the same tick. They
    were simply never handed to the page that exists to answer "am I up or
    down", which left the owner subtracting the dashboard's price from the
    position's cost by hand.

    Fetched in one query rather than per row: the positions page is the one
    screen that renders every open symbol at once.
    """
    if not positions:
        return []

    quotes = {
        quote.symbol: quote
        for quote in db.query(MarketQuote)
        .filter(MarketQuote.symbol.in_({p.symbol for p in positions}))
        .all()
    }

    valued = []
    for position in positions:
        read = PositionRead.model_validate(position)
        quote = quotes.get(position.symbol)
        if quote is not None and quote.price is not None:
            read.current_price = quote.price
            read.market_value = position.quantity * quote.price
            read.unrealized_pnl = (quote.price - position.avg_entry_price) * position.quantity
            if position.avg_entry_price > 0:
                read.unrealized_pnl_pct = (
                    (quote.price - position.avg_entry_price) / position.avg_entry_price
                ) * 100
            read.quote_time = quote.quote_time or quote.fetched_at
        valued.append(read)
    return valued


@router.get("", response_model=list[PositionRead])
def list_positions(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> list[PositionRead]:
    positions = (
        db.query(Position)
        .filter(Position.user_id == user.id, Position.quantity != 0)
        .order_by(Position.symbol)
        .all()
    )
    return _valued(db, positions)


@router.get("/{symbol}", response_model=PositionRead)
def get_position(
    symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> PositionRead:
    return _valued(db, [_get_owned_position(db, user, symbol)])[0]


@router.patch("/{symbol}", response_model=PositionRead)
def adjust_position(
    symbol: str,
    payload: PositionAdjust,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Position:
    # This CREATES a position from the path parameter, and the market loop
    # then polls it and checks its stop-loss. A company name here is a position
    # that never prices and whose stop is never checked; a bare Taiwanese code
    # is worse, because Yahoo prices it as an unrelated Japanese company and the
    # stop IS checked, against the wrong company. Every other symbol entrance
    # was taught to refuse both; this one was missed.
    cleaned = symbol_search.normalise(symbol)
    problem = symbol_search.looks_unpriceable(cleaned)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    position = (
        db.query(Position).filter(Position.user_id == user.id, Position.symbol == cleaned).first()
    )
    if position is None:
        position = Position(user_id=user.id, symbol=cleaned)
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
    # Same reasoning as apply_fill's flat branch: a position nobody holds has
    # no owning strategy, and leaving one behind would show the next re-open
    # under the wrong settings.
    position.strategy_id = None
    db.commit()
