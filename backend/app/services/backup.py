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
import json
import struct
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy.orm import Session

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
