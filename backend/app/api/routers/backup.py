from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.services import backup

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupRequest(BaseModel):
    # The passphrase never leaves this request -- it is not stored anywhere,
    # which is exactly why losing it means losing the archive. Said out loud
    # on the page rather than discovered later.
    passphrase: str = Field(min_length=backup.MIN_PASSPHRASE_LENGTH, max_length=256)


# POST rather than GET: the passphrase must not end up in a URL, a server log,
# or the browser's history.
@router.post("")
def download_backup(
    payload: BackupRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Response:
    try:
        blob = backup.create(db, user, payload.passphrase)
    except backup.BackupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="trading-backup-{stamp}.bak"'},
    )
