from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.models.watchlist import WatchlistItem
from app.schemas.watchlist import WatchlistItemCreate, WatchlistItemRead

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItemRead])
def list_watchlist(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> list[WatchlistItem]:
    # Insertion order, not alphabetical: the owner puts the one they care
    # about first, and re-sorting would take that away every render.
    return (
        db.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.id)
        .all()
    )


@router.post("", response_model=WatchlistItemRead, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    payload: WatchlistItemCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> WatchlistItem:
    existing = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id, WatchlistItem.symbol == payload.symbol)
        .first()
    )
    if existing is not None:
        # Not a conflict: pressing add on something already watched should
        # leave the list as it is, because nothing about the intent was wrong.
        response.status_code = status.HTTP_200_OK
        return existing

    item = WatchlistItem(user_id=user.id, symbol=payload.symbol, data_source=payload.data_source)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(
    symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    item = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol.upper())
        .first()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not on the watchlist")
    db.delete(item)
    db.commit()
