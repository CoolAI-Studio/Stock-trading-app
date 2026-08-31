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


def test_the_archive_opens_with_the_passphrase_alone(db_session):
    """**這個備份不需要部署那把金鑰就打得開。**

    這個模組的檔頭本來就寫著判準：「一份只能用它所備份的那台伺服器上的秘密才打得開的
    備份，不是任何東西的備份——如果失去的正是那台伺服器，那把金鑰也一起沒了。」

    而 _snapshot 裡有一句註解說 config_encrypted 是「原樣帶走、仍然用部署的
    SECRET_ENCRYPTION_KEY 加密」。**那句話是錯的**：那一欄的型別是 EncryptedJSON，
    SQLAlchemy 在讀取時就已經解密，所以進到備份裡的是明文——而它被整份 passphrase 封
    套保護著，跟其他每一欄一樣。

    那句錯的註解已經騙過三個地方：inspect_backup.py 的結尾說明、DEPLOYMENT.md 的還原
    章節，以及備份面板上一段我剛加上去、告訴使用者「這個檔案救不回通知設定」的紅字。
    最後那一個最糟——它會讓一個備份做對了的人以為自己白做了。

    所以這一條**驗行為不驗註解**：真的建一個帶秘密的通知管道、真的備份、真的只用
    passphrase 讀回來，然後看那個 token 在不在。
    """
    user = _seed(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="mine",
            config_encrypted={"bot_token": "a-token-that-must-survive"},
        )
    )
    db_session.commit()

    blob = backup.create(db_session, user, "a-long-enough-passphrase")
    # 只給 passphrase。部署那把金鑰完全沒有參與。
    restored = backup.read(blob, "a-long-enough-passphrase")

    channel = next(c for c in restored["notification_channels"] if c["label"] == "mine")
    assert channel["config_encrypted"] == {"bot_token": "a-token-that-must-survive"}


def test_the_archive_still_hides_that_token_from_anyone_without_the_passphrase(db_session):
    """上一條的另一半，而少了它上一條會變成一個危險的目標。

    「明文放進封套」跟「明文躺在檔案裡」只差一個封套，而那個差別就是這個檔案能不能放
    進雲端硬碟。所以要同時驗：passphrase 打得開，而**沒有 passphrase 的人看不到那個
    token 的任何一個位元組**。
    """
    user = _seed(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="mine",
            config_encrypted={"bot_token": "a-token-that-must-survive"},
        )
    )
    db_session.commit()

    blob = backup.create(db_session, user, "a-long-enough-passphrase")

    assert b"a-token-that-must-survive" not in blob
