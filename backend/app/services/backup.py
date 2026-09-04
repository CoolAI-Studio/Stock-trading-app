"""An encrypted backup the owner can hold themselves.

DEPLOYMENT.md documents a manual pg_dump and says, correctly, that an
unverified backup is not a backup. Both the dump and the verification are
things a person has to remember, and Neon's free tier keeps only a few hours
of point-in-time recovery -- so on the day it is needed the newest copy could
easily be months old.

**Encrypted with the owner's own passphrase, not the deployment's key.** Two
separate reasons, and both matter:

- The archive carries notification tokens (broker credentials are not in it at
  all). Those are encrypted at rest precisely so they are never lying around in
  the clear, and a backup that undoes that is a liability wherever it ends up --
  a downloads folder, an email, someone's cloud drive. The passphrase envelope
  is what keeps that true here: inside it the values are plain, outside it not
  one byte of them is readable.
- A backup that can only be opened with a secret stored on the server it is
  backing up is not a backup of anything. If that server is what was lost, so
  is the key.

Scrypt for the key derivation rather than a plain hash: a person's passphrase
is low-entropy by nature, and the memory-hard parameters are what stop a
stolen archive being brute-forced cheaply. A fresh salt per archive, so two
backups of identical data do not produce identical bytes.
"""

import base64
import hashlib
import json
import struct
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.enums import OrderStatus
from app.models.notification import NotificationChannel
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User
from app.models.watchlist import WatchlistItem

# Bumped when the archive's shape changes in a way a reader must know about.
# Written into the file so a restore reading a format it does not understand
# can say so, instead of half-importing.
FORMAT_VERSION = 1

_MAGIC = b"TRADEBAK"
_SALT_BYTES = 16
# Deliberately expensive. A passphrase somebody can remember is low-entropy,
# and these parameters are the difference between a stolen archive being worth
# attacking and not. ~100ms per attempt on ordinary hardware.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1

MIN_PASSPHRASE_LENGTH = 8


class BackupError(Exception):
    """The archive could not be read: wrong passphrase, truncated, or not one
    of ours. Deliberately one error for all three -- telling an attacker which
    of those it was is free information."""


def _key(passphrase: str, salt: bytes) -> bytes:
    derived = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )
    return base64.urlsafe_b64encode(derived)


def _plain(value: Any) -> Any:
    """JSON cannot hold a Decimal or a datetime; both are round-tripped as
    strings rather than floats, because a float would quietly change a price."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):  # StrEnum and friends
        return value.value
    return value


def _rows(objects, fields: tuple[str, ...]) -> list[dict]:
    return [{field: _plain(getattr(obj, field)) for field in fields} for obj in objects]


def _snapshot(db: Session, user: User) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        # No hashed_password: nothing is gained by carrying it, and it is the
        # one value whose leak compromises the account itself.
        "account": {"email": user.email, "timezone": user.timezone},
        "strategies": _rows(
            db.query(Strategy).filter(Strategy.user_id == user.id).order_by(Strategy.id).all(),
            (
                "id",
                "name",
                "symbol",
                "data_source",
                "source_code",
                "is_active",
                "alert_only",
                "default_quantity",
                "warmup_bars",
                "capital",
                "stop_loss_pct",
                "take_profit_pct",
                "max_position_qty",
                "max_order_notional",
                "max_pending_orders_per_symbol",
                "signal_cooldown_sec",
                "alert_interval_sec",
                "created_at",
            ),
        ),
        "orders": _rows(
            db.query(Order).filter(Order.user_id == user.id).order_by(Order.id).all(),
            (
                "id",
                "strategy_id",
                "source",
                "symbol",
                "side",
                "quantity",
                "signal_price",
                "status",
                "fill_price",
                "filled_quantity",
                "filled_at",
                "created_at",
            ),
        ),
        "positions": _rows(
            db.query(Position).filter(Position.user_id == user.id).order_by(Position.id).all(),
            ("symbol", "quantity", "avg_entry_price", "realized_pnl", "opened_at", "strategy_id"),
        ),
        "risk_settings": _rows(
            db.query(RiskSettings).filter(RiskSettings.user_id == user.id).all(),
            (
                "capital",
                "stop_loss_pct",
                "take_profit_pct",
                "max_position_qty",
                "max_order_notional",
                "max_pending_orders_per_symbol",
                "signal_cooldown_sec",
                "alert_interval_sec",
            ),
        ),
        # config_encrypted arrives here ALREADY DECRYPTED: the column type is
        # EncryptedJSON, so SQLAlchemy decrypts on load and this sees the dict.
        # What lands in the archive is the plaintext, inside the passphrase
        # envelope like every other field -- which is exactly what the module
        # docstring's second bullet requires, and it holds.
        #
        # This comment used to say the opposite ("carried as stored, still
        # encrypted, so a restore needs that key too"). It was wrong, and being
        # wrong in a comment cost more than being wrong in code would have: it
        # was copied into inspect_backup.py's closing note, into DEPLOYMENT.md's
        # restore section, and finally onto the backup panel itself, where it
        # told somebody who HAD backed up correctly that their archive could not
        # restore their notification settings. Pinned now by
        # tests/test_backup.py::test_the_archive_opens_with_the_passphrase_alone,
        # which builds a real channel and reads it back with the passphrase and
        # nothing else.
        "notification_channels": _rows(
            db.query(NotificationChannel)
            .filter(NotificationChannel.user_id == user.id)
            .order_by(NotificationChannel.id)
            .all(),
            (
                "channel_type",
                "label",
                "is_enabled",
                "subscribed_events",
                "quiet_start_hour",
                "quiet_end_hour",
                "config_encrypted",
            ),
        ),
        "watchlist": _rows(
            db.query(WatchlistItem)
            .filter(WatchlistItem.user_id == user.id)
            .order_by(WatchlistItem.id)
            .all(),
            ("symbol", "data_source"),
        ),
        "alerts": _rows(
            db.query(StrategyAlert)
            .filter(StrategyAlert.user_id == user.id)
            .order_by(StrategyAlert.id)
            .all(),
            ("strategy_id", "symbol", "side", "price", "status", "created_at"),
        ),
    }


def create(db: Session, user: User, passphrase: str) -> bytes:
    """The encrypted archive, ready to be written to a file."""
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise BackupError(f"密碼至少要 {MIN_PASSPHRASE_LENGTH} 個字")

    import os

    salt = os.urandom(_SALT_BYTES)
    payload = json.dumps(_snapshot(db, user), ensure_ascii=False).encode("utf-8")
    token = Fernet(_key(passphrase, salt)).encrypt(payload)
    # magic + version + salt, then the ciphertext. The header is plaintext on
    # purpose: a reader has to know the salt before it can derive anything.
    return _MAGIC + struct.pack("!H", FORMAT_VERSION) + salt + token


def read(blob: bytes, passphrase: str) -> dict:
    header = len(_MAGIC) + 2 + _SALT_BYTES
    if len(blob) <= header or not blob.startswith(_MAGIC):
        raise BackupError("這個檔案不是這個系統產生的備份，或已經損毀。")

    salt = blob[len(_MAGIC) + 2 : header]
    try:
        payload = Fernet(_key(passphrase, salt)).decrypt(blob[header:])
    except (InvalidToken, ValueError) as exc:
        raise BackupError("密碼不對，或檔案已經損毀。") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover -- decryption implies valid JSON
        raise BackupError("備份內容無法解讀。") from exc


# ---------------------------------------------------------------------------
# 還原
# ---------------------------------------------------------------------------
#
# ＊ 這裡的每一條規則都來自同一個判斷：**不可以丟東西，也不可以自己動起來。**
#
# 「還原」在使用者腦中的意思是「把備份裡的東西拿回來」，不是「把現在這一份換掉」。所
# 以這裡一律**新增**，從不覆寫、從不刪除——他按錯了也只是多了一些東西，而多出來的東西
# 刪得掉，被蓋掉的東西回不來。
#
# 但「一律新增」單獨拿出來會製造一個更糟的問題：兩份一樣的策略同時在跑，同一件事通知
# 兩次；兩份一樣的持股，系統以為他部位加倍，停損和風控全部算錯。所以每一張表要分開想：
#
#   策略、通知管道      新增，但**一律是停用的**。他自己打開他要的那幾個。
#   持股、自選股        有 UNIQUE(user, symbol)，所以「沒有的才加」。重複會讓數字是錯的。
#   風控設定            一個使用者一份。只在完全沒有的時候才建，絕不蓋掉他現在那一份。
#   訊號紀錄、提醒紀錄  純歷史，全部加回來；待確認的訊號改成已過期（見下面）。
#   帳號本身            完全不動。資料掛到**現在登入的這個人**底下。
#
# ＊ 為什麼策略要改名。
#
# `UNIQUE(user_id, name)`——不改名的話還原會直接撞上約束，然後整批失敗。而就算沒有那
# 個約束也要改：畫面上兩個一模一樣的名字，他分不出哪一個是還原進來的。

_RESTORED_SUFFIX = "（還原 {stamp}）"


class _Coercer:
    """把 JSON 裡的字串換回欄位真正的型別。

    `_plain` 出去的時候把 Decimal、datetime、Enum 都變成了字串（float 會悄悄改掉一個
    價格，所以不能用）。回來的路照著欄位自己宣告的型別走，而不是在這裡手抄一份清單
    ——手抄的那一份會在有人加欄位的時候悄悄過期，而症狀是還原回來的價格變成字串。
    """

    def __init__(self, model) -> None:
        self._columns = {column.key: column for column in sa_inspect(model).columns}

    def __call__(self, field: str, value: Any) -> Any:
        column = self._columns.get(field)
        if value is None or column is None:
            return value
        try:
            python_type = column.type.python_type
        except (NotImplementedError, AttributeError):
            return value
        if python_type is Decimal:
            return Decimal(str(value))
        if python_type is datetime:
            return datetime.fromisoformat(value) if isinstance(value, str) else value
        if (
            isinstance(value, str)
            and isinstance(python_type, type)
            and issubclass(python_type, Enum)
        ):
            return python_type(value)
        return value

    def build(self, model, row: dict, skip: tuple[str, ...] = (), **overrides):
        fields = {k: self(k, v) for k, v in row.items() if k not in skip and k in self._columns}
        return model(**{**fields, **overrides})


@dataclass(frozen=True)
class RestoreReport:
    """還原完之後畫面上要說的話。

    數字要分開講，不能只說「還原完成」：他最需要知道的是「哪些東西是停用的、等他打
    開」，而那件事沒有說出口的話，他會以為提醒已經在跑了。
    """

    strategies: int = 0
    channels: int = 0
    orders: int = 0
    alerts: int = 0
    positions: int = 0
    positions_skipped: int = 0
    watchlist: int = 0
    watchlist_skipped: int = 0
    risk_settings_created: bool = False
    expired_pending: int = 0


def restore(db: Session, user: User, snapshot: dict) -> RestoreReport:
    """把備份裡的東西加到這個帳號底下。**只加，不蓋，不刪。**

    回傳一份「做了什麼」的清單，因為「還原完成」四個字說不出他真正需要知道的那件事：
    策略和通知管道是停用的，等他自己打開。
    """
    version = snapshot.get("format_version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        # 比這一版新的檔案不能倒。往回搬沒有安全的做法——我們不知道未來的欄位是什麼
        # 意思，而猜錯的代價是他以為還原好了。
        raise BackupError(
            f"這個備份是比較新的版本（{version}），這一份程式讀不懂。先把系統更新到最新，再試一次。"
        )

    stamp = datetime.now(UTC).strftime("%m-%d %H:%M")
    suffix = _RESTORED_SUFFIX.format(stamp=stamp)
    report = RestoreReport()

    # 策略：新增、停用、改名。舊 id → 新 id 的對應留著給訊號和提醒紀錄用。
    strategy_ids: dict[int, int] = {}
    coerce = _Coercer(Strategy)
    for row in snapshot.get("strategies") or []:
        source = row.get("source_code") or ""
        wanted = str(row.get("name") or "未命名")
        strategy = coerce.build(
            Strategy,
            row,
            skip=("id", "is_active", "name", "code_hash"),
            user_id=user.id,
            name=_unique_name(db, user, wanted + suffix),
            source_code=source,
            code_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            # **一律停用。** 兩份一樣的策略同時在跑 ＝ 同一件事通知兩次，而那是這個
            # 產品最不能發生的事。他打開他要的那幾個。
            is_active=False,
        )
        db.add(strategy)
        db.flush()
        if row.get("id") is not None:
            strategy_ids[int(row["id"])] = strategy.id
        report = replace(report, strategies=report.strategies + 1)

    # 通知管道：同樣一律停用，理由一樣——它們是真的會把東西送出去的那一個。
    coerce = _Coercer(NotificationChannel)
    for row in snapshot.get("notification_channels") or []:
        db.add(
            coerce.build(
                NotificationChannel,
                row,
                skip=("is_enabled",),
                user_id=user.id,
                is_enabled=False,
            )
        )
        report = replace(report, channels=report.channels + 1)

    # 持股：UNIQUE(user, symbol)。重複的話系統會以為他部位加倍，停損和風控全部算錯，
    # 所以「沒有的才加」——而已經有的那一筆是他現在真正持有的，比備份裡的新。
    have = {symbol for (symbol,) in db.query(Position.symbol).filter(Position.user_id == user.id)}
    coerce = _Coercer(Position)
    for row in snapshot.get("positions") or []:
        if row.get("symbol") in have:
            report = replace(report, positions_skipped=report.positions_skipped + 1)
            continue
        db.add(
            coerce.build(
                Position,
                row,
                skip=("strategy_id",),
                user_id=user.id,
                strategy_id=strategy_ids.get(row.get("strategy_id")),
            )
        )
        have.add(row["symbol"])
        report = replace(report, positions=report.positions + 1)

    # 自選股：同樣有 UNIQUE，而且重複在清單上就只是重複。
    watched = {
        symbol
        for (symbol,) in db.query(WatchlistItem.symbol).filter(WatchlistItem.user_id == user.id)
    }
    coerce = _Coercer(WatchlistItem)
    for row in snapshot.get("watchlist") or []:
        if row.get("symbol") in watched:
            report = replace(report, watchlist_skipped=report.watchlist_skipped + 1)
            continue
        db.add(coerce.build(WatchlistItem, row, user_id=user.id))
        watched.add(row["symbol"])
        report = replace(report, watchlist=report.watchlist + 1)

    # 風控：一個使用者一份，所以只在完全沒有的時候才建。**絕不蓋掉他現在那一份**——
    # 那是他的停損設定，用一個舊檔案換掉它，下一次穿價就是照錯的數字算。
    rows = snapshot.get("risk_settings") or []
    if rows and not db.query(RiskSettings).filter(RiskSettings.user_id == user.id).first():
        db.add(_Coercer(RiskSettings).build(RiskSettings, rows[0], user_id=user.id))
        report = replace(report, risk_settings_created=True)

    # 訊號紀錄：純歷史，全部加回來。
    #
    # **但待確認的要改成已過期。** 一張三個月前的待確認訊號被復活成「現在等你確認」，
    # 是拿一個早就過去的價格問他要不要動作——而他不會知道那是舊的。歷史留著，行為不留。
    coerce = _Coercer(Order)
    for row in snapshot.get("orders") or []:
        raw_status = row.get("status")
        was_pending = str(raw_status or "").upper() == OrderStatus.PENDING.value.upper()
        db.add(
            coerce.build(
                Order,
                row,
                skip=("id", "strategy_id", "status"),
                user_id=user.id,
                strategy_id=strategy_ids.get(row.get("strategy_id")),
                status=OrderStatus.EXPIRED if was_pending else coerce("status", raw_status),
            )
        )
        report = replace(report, orders=report.orders + 1)
        if was_pending:
            report = replace(report, expired_pending=report.expired_pending + 1)

    # 提醒紀錄：也是純歷史，沒有任何東西會照著它動作。
    coerce = _Coercer(StrategyAlert)
    for row in snapshot.get("alerts") or []:
        new_id = strategy_ids.get(row.get("strategy_id"))
        if new_id is None:
            # 那支策略不在這個備份裡（或沒有帶 id）。FK 是 NOT NULL，所以只能跳過
            # ——一筆孤兒的提醒紀錄對他也沒有意義。
            continue
        db.add(
            coerce.build(
                StrategyAlert,
                row,
                skip=("strategy_id",),
                user_id=user.id,
                strategy_id=new_id,
            )
        )
        report = replace(report, alerts=report.alerts + 1)

    db.commit()
    return report


def _unique_name(db: Session, user: User, wanted: str) -> str:
    """`UNIQUE(user_id, name)`——同一個備份倒兩次也要能倒。

    撞到就加序號，而不是拋錯：他會倒第二次，多半正是因為第一次沒看清楚做了什麼。
    """
    name = wanted[:120]
    taken = {
        existing for (existing,) in db.query(Strategy.name).filter(Strategy.user_id == user.id)
    }
    if name not in taken:
        return name
    for n in range(2, 1000):
        candidate = f"{name[:114]} {n}"
        if candidate not in taken:
            return candidate
    return f"{name[:106]} {datetime.now(UTC).strftime('%H%M%S%f')}"
