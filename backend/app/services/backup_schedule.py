"""Emailing the encrypted backup on a timer.

Email is the destination that needs no new account: the SMTP channel already
configured for alerts is reused, so there is nothing further to register or
authorise. Google Drive and Dropbox look easier because the owner already has
an account, but neither has an app-password equivalent -- programmatic upload
means registering an OAuth application, which is more setup than it sounds.

The failure this exists to prevent is a backup that silently never arrives, so
every refusal here is recorded on the row rather than only logged, and a
failed send deliberately leaves the schedule due. Marking it done on failure
would mean the owner believes they hold a backup they never received, which is
worse than having no schedule at all.
"""

import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.enums import ChannelType
from app.models.backup import BackupSchedule
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel
from app.models.user import User
from app.services import backup

logger = logging.getLogger(__name__)


def is_due(schedule: BackupSchedule, now: datetime | None = None) -> bool:
    if not schedule.is_enabled:
        return False
    if schedule.last_sent_at is None:
        # Never run: due immediately, so switching it on produces a backup the
        # same day rather than in a week's time.
        return True
    moment = now or utcnow()
    # last_sent_at comes back naive out of SQLite; compare on one footing.
    last = schedule.last_sent_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return moment - last >= timedelta(days=schedule.interval_days)


def _email_config(db: Session, schedule: BackupSchedule) -> dict | None:
    channel = (
        db.query(NotificationChannel)
        .filter(
            NotificationChannel.user_id == schedule.user_id,
            NotificationChannel.channel_type == ChannelType.EMAIL,
            NotificationChannel.is_enabled.is_(True),
        )
        .first()
    )
    if channel is None:
        return None
    config = dict(channel.config_encrypted or {})
    if schedule.to_addr:
        config["to_addr"] = schedule.to_addr
    return config


def _send_email(config: dict, subject: str, body: str, filename: str, attachment: bytes) -> None:
    """Raises on failure. The caller records it; swallowing it here would be
    the silent-backup failure this module exists to prevent."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["from_addr"]
    message["To"] = config["to_addr"]
    message.set_content(body)
    message.add_attachment(
        attachment,
        maintype="application",
        subtype="octet-stream",
        filename=filename,
    )

    with smtplib.SMTP(config["host"], config.get("port", 587), timeout=30) as smtp:
        if config.get("use_tls", True):
            smtp.starttls()
        username, password = config.get("username"), config.get("password")
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def _fail(db: Session, schedule: BackupSchedule, reason: str) -> None:
    schedule.last_error = reason
    db.commit()
    logger.warning("backup for user %s not sent: %s", schedule.user_id, reason)


def run_due(db: Session, now: datetime | None = None) -> int:
    """Send every backup that has come due. Returns how many went out."""
    sent = 0
    for schedule in db.query(BackupSchedule).filter(BackupSchedule.is_enabled.is_(True)).all():
        if not is_due(schedule, now):
            continue

        passphrase = (schedule.passphrase_encrypted or {}).get("value")
        if not passphrase:
            # Never fall back to an unencrypted archive: that would put the
            # whole trading record, in the clear, into an inbox.
            _fail(db, schedule, "沒有設定備份密碼，不會寄出未加密的備份。請重新設定一次。")
            continue

        config = _email_config(db, schedule)
        if not config or not config.get("host") or not config.get("to_addr"):
            _fail(db, schedule, "找不到可用的 Email 通知管道，備份沒有地方可以寄。")
            continue

        user = db.get(User, schedule.user_id)
        if user is None:  # pragma: no cover -- FK cascade makes this unreachable
            continue

        try:
            blob = backup.create(db, user, passphrase)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
            _send_email(
                config,
                f"交易系統備份 {stamp}",
                # **這封信是 app 寄給他的，所以它說的話要對他成立。**
                #
                # 兩個版本都錯過，方向相反：
                #
                # 一、原本寫「要檢視內容：python scripts/inspect_backup.py」。收
                #     信的人是按按鈕部署的——他手上沒有這個 repo、沒有 Python、
                #     也沒有終端機。而這一封特別諷刺：整個排程備份存在的理由就是
                #     讓他不用記得去做一件手動的事，然後信裡叫他做一件他做不到的。
                #
                # 二、改成「到 app 的『帳號』頁，備份那一區有『從備份還原』」——
                #     **那顆按鈕從來沒有被做出來。** 那比第一版更糟：第一版至少對
                #     真的會用終端機的人是對的，第二版對任何人都不成立，而他讀到
                #     它的時機是已經弄丟東西的那一天。
                #
                # 三、現在那顆按鈕真的做出來了（#81），所以這封信終於可以指向一個
                #     存在的地方。**指路之前先確認那條路存在**——上面兩次錯的都是這
                #     一點，而不是文案。
                #
                # 還原的語意也一起講：它只加不蓋，而加回來的策略和通知管道是停用
                # 的。不講的話，他會在最緊張的那一天按下去，然後不知道為什麼提醒
                # 沒有回來。
                (
                    "附件是這個帳號的加密備份。把它存起來就好，不用打開它。\n\n"
                    "用你設定備份時輸入的密碼才能打開；那組密碼沒有存在這封信裡，"
                    "也不會出現在任何郵件中——弄丟就打不開這些檔案了。\n\n"
                    "這個檔案是完整的：它需要的只有那組密碼，不需要你這份部署的任何"
                    "金鑰。所以就算整台機器沒了，資料還在你手上。\n\n"
                    "要放回去的時候：打開你的 app → 風險設定 → 「從備份還原」，"
                    "選這個檔案、輸入那組密碼就好。\n\n"
                    "還原只會「加」，不會刪掉或蓋掉你當時已經有的東西；而加回來的"
                    "策略和通知管道會是停用的，你自己打開要用的那幾個——這樣同一支"
                    "策略才不會有兩份同時在跑、同一件事通知你兩次。\n"
                ),
                f"trading-backup-{stamp}.bak",
                blob,
            )
        except Exception as exc:
            # Deliberately left due: marking it done would mean the owner
            # believes they hold a backup they never received.
            _fail(db, schedule, str(exc)[:500])
            continue

        schedule.last_sent_at = utcnow()
        schedule.last_error = None
        db.commit()
        sent += 1
        logger.info("backup emailed for user %s", schedule.user_id)

    return sent
