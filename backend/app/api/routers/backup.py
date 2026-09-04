from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.backup import BackupSchedule
from app.models.user import User
from app.schemas.common import UtcDatetime
from app.services import backup

router = APIRouter(prefix="/backup", tags=["backup"])

# 一個人的交易紀錄不會有幾十 MB。沒有上限的話，這支端點就是一個把記憶體吃光的方法
# ——而這台機器上跑著他所有的提醒。
_MAX_RESTORE_BYTES = 32 * 1024 * 1024


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


class RestoreReport(BaseModel):
    """做了什麼，逐項說。

    **不可以只回「還原完成」。** 他最需要知道的那件事是「策略和通知管道是停用的，
    等你打開」——沒有說出口的話，他會以為提醒已經在跑了，而那正是這個產品唯一不能
    失效的東西。
    """

    strategies: int
    channels: int
    orders: int
    alerts: int
    positions: int
    positions_skipped: int
    watchlist: int
    watchlist_skipped: int
    risk_settings_created: bool
    expired_pending: int


# 檔案上傳，所以是 multipart 而不是 JSON。密碼跟著同一個請求走（表單欄位，不是網址
# 參數）——理由跟上面那個 POST 一樣：不可以進網址、log 或瀏覽紀錄。
@router.post("/restore", response_model=RestoreReport)
def restore_backup(
    file: Annotated[UploadFile, File()],
    passphrase: Annotated[str, Form(min_length=backup.MIN_PASSPHRASE_LENGTH, max_length=256)],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> RestoreReport:
    """把備份檔裡的東西加到這個帳號底下。

    ＊ 這支端點不會刪掉任何東西。

    `backup.restore` 一律新增、從不覆寫（那個模組裡有整段理由）。所以這裡沒有「確定
    要覆蓋嗎」那種確認——因為沒有東西會被覆蓋。他按錯了只是多了一些停用的東西。

    ＊ 大小上限。

    備份是一份 JSON，一個人的交易紀錄不會有幾十 MB；沒有上限的話，這支需要登入的端
    點會變成一個把整台機器的記憶體吃光的方法。
    """
    blob = file.file.read(_MAX_RESTORE_BYTES + 1)
    if len(blob) > _MAX_RESTORE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="這個檔案太大，不像是這個系統產生的備份。",
        )

    try:
        snapshot = backup.read(blob, passphrase)
        report = backup.restore(db, user, snapshot)
    except backup.BackupError as exc:
        # 密碼不對、檔案損毀、版本太新——都是他改得動的狀況，所以原話回去。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return RestoreReport(**asdict(report))


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
