from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.backup import BackupSchedule
from app.models.user import User
from app.schemas.common import UtcDatetime
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


class BackupScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    is_enabled: bool
    interval_days: int
    to_addr: str | None
    last_sent_at: UtcDatetime | None
    last_error: str | None
    # Whether one is set, never what it is. Reading it back would put it in
    # every browser cache for no gain -- the worker is the only thing that
    # needs the value.
    has_passphrase: bool


class BackupScheduleUpdate(BaseModel):
    is_enabled: bool
    interval_days: int = Field(ge=1, le=365)
    to_addr: str | None = Field(default=None, max_length=255)
    # Omitted means "leave the stored one alone", so changing the interval does
    # not force the owner to retype a passphrase they may not have to hand.
    passphrase: str | None = Field(default=None, min_length=backup.MIN_PASSPHRASE_LENGTH)


def _get_or_create(db: Session, user: User) -> BackupSchedule:
    schedule = db.query(BackupSchedule).filter(BackupSchedule.user_id == user.id).first()
    if schedule is None:
        schedule = BackupSchedule(user_id=user.id)
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
    return schedule


def _to_read(schedule: BackupSchedule) -> BackupScheduleRead:
    return BackupScheduleRead(
        is_enabled=schedule.is_enabled,
        interval_days=schedule.interval_days,
        to_addr=schedule.to_addr,
        last_sent_at=schedule.last_sent_at,
        last_error=schedule.last_error,
        has_passphrase=bool((schedule.passphrase_encrypted or {}).get("value")),
    )


@router.get("/schedule", response_model=BackupScheduleRead)
def read_schedule(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> BackupScheduleRead:
    return _to_read(_get_or_create(db, user))


@router.put("/schedule", response_model=BackupScheduleRead)
def update_schedule(
    payload: BackupScheduleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> BackupScheduleRead:
    schedule = _get_or_create(db, user)

    if payload.passphrase:
        schedule.passphrase_encrypted = {"value": payload.passphrase}
    if payload.is_enabled and not (schedule.passphrase_encrypted or {}).get("value"):
        # Refused rather than silently sending an unencrypted archive: that
        # would put the whole trading record, in the clear, into an inbox.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="要開啟自動備份，必須先設定一組備份密碼。",
        )

    schedule.is_enabled = payload.is_enabled
    schedule.interval_days = payload.interval_days
    schedule.to_addr = payload.to_addr
    db.commit()
    db.refresh(schedule)
    return _to_read(schedule)
