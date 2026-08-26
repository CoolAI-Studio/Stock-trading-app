"""A backup the owner can actually hold.

DEPLOYMENT.md documents a manual pg_dump and says, correctly, that an
unverified backup is not a backup. Both the dump and the verification are
things a person has to remember, and Neon's free tier keeps only a few hours
of point-in-time recovery -- so on the day it is needed the newest copy could
easily be months old.

The file is encrypted with a passphrase the owner chooses, not with the
deployment's own key. Two reasons: the archive contains broker API keys and
notification tokens, which are encrypted at rest precisely so they are never
lying around in the clear; and a backup that can only be opened with a secret
stored on the server it is backing up is not a backup of anything.
"""

from decimal import Decimal

import pytest

from app.enums import ChannelType, OrderSide, OrderSource, OrderStatus
from app.models.notification import NotificationChannel
from app.models.order import Order
from app.models.strategy import Strategy
from app.models.user import User
from app.services import backup

PASSPHRASE = "a-long-enough-passphrase"


def _seed(db_session) -> User:
    # auth_client makes one; a db_session-only test does not.
    user = db_session.query(User).first()
    if user is None:
        user = User(email="backup@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    db_session.add(
        Strategy(
            user_id=user.id,
            name="my-strategy",
            symbol="2330.TW",
            source_code="class Strategy: pass",
            code_hash="h",
        )
    )
    db_session.add(
        Order(
            user_id=user.id,
            source=OrderSource.MANUAL,
            symbol="2330.TW",
            side=OrderSide.BUY,
            quantity=Decimal(1000),
            status=OrderStatus.CONFIRMED,
            fill_price=Decimal(1000),
            filled_quantity=Decimal(1000),
        )
    )
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="phone",
            config_encrypted={"bot_token": "super-secret-token", "chat_id": "1"},
        )
    )
    db_session.commit()
    return user


# --- the archive itself -----------------------------------------------------


def test_a_backup_round_trips(db_session):
    user = _seed(db_session)

    blob = backup.create(db_session, user, PASSPHRASE)
    restored = backup.read(blob, PASSPHRASE)

    assert restored["strategies"][0]["name"] == "my-strategy"
    assert restored["orders"][0]["symbol"] == "2330.TW"


def test_the_wrong_passphrase_does_not_open_it(db_session):
    user = _seed(db_session)
    blob = backup.create(db_session, user, PASSPHRASE)

    with pytest.raises(backup.BackupError):
        backup.read(blob, "not-the-passphrase")


def test_the_file_is_not_readable_without_the_passphrase(db_session):
    """The whole point. A backup with the strategy source and a Telegram token
    sitting in the clear is a liability wherever it ends up."""
    user = _seed(db_session)
    blob = backup.create(db_session, user, PASSPHRASE)

    assert b"my-strategy" not in blob
    assert b"super-secret-token" not in blob
    assert b"2330.TW" not in blob


def test_each_backup_uses_a_fresh_salt(db_session):
    """Two backups of identical data must not produce identical bytes -- a
    fixed salt would let anyone with two files learn something about the
    passphrase, and would make a rainbow table worth building."""
    user = _seed(db_session)

    first = backup.create(db_session, user, PASSPHRASE)
    second = backup.create(db_session, user, PASSPHRASE)

    assert first != second
    assert (
        backup.read(first, PASSPHRASE)["strategies"]
        == backup.read(second, PASSPHRASE)["strategies"]
    )


def test_a_truncated_file_fails_cleanly_rather_than_raising_something_odd(db_session):
    user = _seed(db_session)
    blob = backup.create(db_session, user, PASSPHRASE)

    with pytest.raises(backup.BackupError):
        backup.read(blob[:20], PASSPHRASE)


def test_the_archive_says_which_version_wrote_it(db_session):
    """A restore script reading a format it does not understand must be able
    to say so, rather than half-importing."""
    user = _seed(db_session)
    restored = backup.read(backup.create(db_session, user, PASSPHRASE), PASSPHRASE)

    assert restored["format_version"] >= 1
    assert restored["created_at"]


def test_only_the_owners_own_rows_are_in_it(db_session):
    from app.core.security import hash_password

    user = _seed(db_session)
    other = User(email="nosy@example.com", hashed_password=hash_password("pw12345678"))
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    db_session.add(
        Strategy(
            user_id=other.id,
            name="not-yours",
            symbol="AAPL",
            source_code="class Strategy: pass",
            code_hash="h",
        )
    )
    db_session.commit()

    restored = backup.read(backup.create(db_session, user, PASSPHRASE), PASSPHRASE)
    assert [s["name"] for s in restored["strategies"]] == ["my-strategy"]


def test_the_password_hash_is_not_in_the_backup(db_session):
    """Nothing is gained by carrying it and it is the one value whose leak
    compromises the account itself."""
    user = _seed(db_session)
    restored = backup.read(backup.create(db_session, user, PASSPHRASE), PASSPHRASE)

    assert "hashed_password" not in restored["account"]
    assert restored["account"]["email"] == user.email


# --- the endpoint -----------------------------------------------------------


def test_the_download_needs_a_passphrase(auth_client):
    resp = auth_client.post("/api/backup", json={"passphrase": "short"})
    assert resp.status_code == 422


def test_the_download_returns_a_file(auth_client, db_session):
    _seed(db_session)

    resp = auth_client.post("/api/backup", json={"passphrase": PASSPHRASE})
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert backup.read(resp.content, PASSPHRASE)["strategies"]


def test_the_backup_endpoint_needs_a_login(client):
    assert client.post("/api/backup", json={"passphrase": PASSPHRASE}).status_code == 401
