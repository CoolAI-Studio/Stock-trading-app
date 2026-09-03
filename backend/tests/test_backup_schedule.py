"""A backup that arrives without anybody remembering to fetch it.

The download button is only as good as the habit, and a backup habit is
exactly the kind that lapses -- the free-tier database keeps a few hours of
point-in-time recovery, so the gap between "I meant to do that" and "I needed
that" is where the whole year's records go.

Emailed, because that is the destination that needs no new account: the SMTP
channel the owner already set up for alerts is reused, so there is nothing
further to register or authorise.

The uncomfortable part, said plainly rather than hidden: automating the
encryption means the passphrase has to live on the server. It is stored the
same way broker keys are -- encrypted at rest -- but if the whole deployment
is what was lost, that copy is gone with it. The owner has to keep their own
note of it, and the form says so.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

from app.enums import ChannelType
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel
from app.models.user import User
from app.services import backup, backup_schedule

PASSPHRASE = "a-long-enough-passphrase"


def _user(db_session) -> User:
    user = db_session.query(User).first()
    if user is None:
        user = User(email="sched@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _email_channel(db_session, user) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.EMAIL,
        label="mail",
        config_encrypted={
            "host": "smtp.example.com",
            "port": 587,
            "username": "me@example.com",
            "password": "app-password",
            "from_addr": "me@example.com",
            "to_addr": "me@example.com",
        },
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _schedule(db_session, user, **kw):
    from app.models.backup import BackupSchedule

    defaults = dict(is_enabled=True, interval_days=7, passphrase_encrypted={"value": PASSPHRASE})
    defaults.update(kw)
    schedule = BackupSchedule(user_id=user.id, **defaults)
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


# --- when it is due ---------------------------------------------------------


def test_a_schedule_that_has_never_run_is_due_immediately(db_session):
    user = _user(db_session)
    schedule = _schedule(db_session, user, last_sent_at=None)
    assert backup_schedule.is_due(schedule)


def test_a_recent_backup_is_not_due(db_session):
    user = _user(db_session)
    schedule = _schedule(db_session, user, last_sent_at=utcnow() - timedelta(days=1))
    assert not backup_schedule.is_due(schedule)


def test_it_comes_due_again_after_the_interval(db_session):
    user = _user(db_session)
    schedule = _schedule(db_session, user, last_sent_at=utcnow() - timedelta(days=8))
    assert backup_schedule.is_due(schedule)


def test_a_disabled_schedule_is_never_due(db_session):
    user = _user(db_session)
    schedule = _schedule(db_session, user, is_enabled=False, last_sent_at=None)
    assert not backup_schedule.is_due(schedule)


# --- sending ----------------------------------------------------------------


def test_the_backup_is_sent_as_an_attachment(db_session):
    user = _user(db_session)
    _email_channel(db_session, user)
    _schedule(db_session, user)

    with patch.object(backup_schedule, "_send_email", return_value=None) as send:
        backup_schedule.run_due(db_session)

    assert send.call_count == 1
    _config, subject, _body, filename, blob = send.call_args.args
    assert "備份" in subject
    assert filename.endswith(".bak")
    # Openable with the stored passphrase, which is the only thing that makes
    # the emailed copy worth anything.
    assert backup.read(blob, PASSPHRASE)["account"]["email"] == user.email


def test_a_successful_send_marks_when_it_happened(db_session):
    user = _user(db_session)
    _email_channel(db_session, user)
    schedule = _schedule(db_session, user)

    with patch.object(backup_schedule, "_send_email", return_value=None):
        backup_schedule.run_due(db_session)

    db_session.refresh(schedule)
    assert schedule.last_sent_at is not None
    assert schedule.last_error is None


def test_a_failed_send_is_recorded_and_left_due(db_session):
    """Marking it done on failure would mean the owner believes they have a
    backup they never received -- the worst possible outcome for this feature."""
    user = _user(db_session)
    _email_channel(db_session, user)
    schedule = _schedule(db_session, user)

    with patch.object(backup_schedule, "_send_email", side_effect=OSError("smtp refused")):
        backup_schedule.run_due(db_session)

    db_session.refresh(schedule)
    assert schedule.last_sent_at is None, "still owed"
    assert "smtp" in (schedule.last_error or "")
    assert backup_schedule.is_due(schedule)


def test_nothing_is_sent_without_an_email_channel_to_send_it_through(db_session):
    user = _user(db_session)
    schedule = _schedule(db_session, user)

    with patch.object(backup_schedule, "_send_email") as send:
        backup_schedule.run_due(db_session)

    assert send.call_count == 0
    db_session.refresh(schedule)
    assert "Email" in (schedule.last_error or "")


def test_a_schedule_with_no_passphrase_does_not_send_a_plaintext_backup(db_session):
    """Falling back to no encryption would put the whole trading record, in
    the clear, into an inbox."""
    user = _user(db_session)
    _email_channel(db_session, user)
    schedule = _schedule(db_session, user, passphrase_encrypted=None)

    with patch.object(backup_schedule, "_send_email") as send:
        backup_schedule.run_due(db_session)

    assert send.call_count == 0
    db_session.refresh(schedule)
    assert schedule.last_error


def test_it_can_be_sent_somewhere_other_than_the_alert_address(db_session):
    user = _user(db_session)
    _email_channel(db_session, user)
    _schedule(db_session, user, to_addr="archive@example.com")

    with patch.object(backup_schedule, "_send_email", return_value=None) as send:
        backup_schedule.run_due(db_session)

    config = send.call_args.args[0]
    assert config["to_addr"] == "archive@example.com"


# --- the API ----------------------------------------------------------------


def test_the_schedule_can_be_read_and_set(auth_client):
    assert auth_client.get("/api/backup/schedule").json()["is_enabled"] is False

    resp = auth_client.put(
        "/api/backup/schedule",
        json={"is_enabled": True, "interval_days": 7, "passphrase": PASSPHRASE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_enabled"] is True


def test_the_passphrase_never_comes_back_out(auth_client):
    """It is stored so the worker can use it; reading it back would put it in
    every browser cache for no gain."""
    auth_client.put(
        "/api/backup/schedule",
        json={"is_enabled": True, "interval_days": 7, "passphrase": PASSPHRASE},
    )

    body = auth_client.get("/api/backup/schedule").json()
    assert PASSPHRASE not in str(body)
    assert body["has_passphrase"] is True


def test_turning_it_on_without_a_passphrase_is_refused(auth_client):
    resp = auth_client.put("/api/backup/schedule", json={"is_enabled": True, "interval_days": 7})
    assert resp.status_code == 422


def test_the_passphrase_can_be_left_alone_when_changing_the_interval(auth_client):
    auth_client.put(
        "/api/backup/schedule",
        json={"is_enabled": True, "interval_days": 7, "passphrase": PASSPHRASE},
    )
    resp = auth_client.put("/api/backup/schedule", json={"is_enabled": True, "interval_days": 30})

    assert resp.status_code == 200
    assert resp.json()["interval_days"] == 30
    assert resp.json()["has_passphrase"] is True


@pytest.mark.parametrize("days", [0, 400])
def test_a_nonsense_interval_is_refused(auth_client, days):
    resp = auth_client.put(
        "/api/backup/schedule",
        json={"is_enabled": True, "interval_days": days, "passphrase": PASSPHRASE},
    )
    assert resp.status_code == 422


def test_the_schedule_needs_a_login(client):
    assert client.get("/api/backup/schedule").status_code == 401


def test_the_email_does_not_tell_him_to_run_a_python_script():
    """**這封信是 app 自己寄給他的，所以它說的話要對他成立。**

    內文原本寫著「要檢視內容：python scripts/inspect_backup.py 這個檔案」。而收信的人
    是按按鈕部署的——他手上沒有這個 repo、沒有 Python、也沒有終端機。CLAUDE.md 的使用
    者規則第一條：「任何『請在你的電腦上跑這支腳本』的指示，對這個使用者等於流程到此
    結束。」

    這一封特別諷刺：整個排程備份存在的理由，就是讓他不用記得去做一件手動的事；然後信
    裡叫他做一件他做不到的事。

    腳本沒有被拿掉（對真的在命令列上的人它是對的），改的是**先講他做得到的那一個**。
    """
    from app.services import backup_schedule

    source = __import__("pathlib").Path(backup_schedule.__file__).read_text(encoding="utf-8")
    body_start = source.index("附件是這個帳號的加密備份")
    body = source[body_start : source.index('f"trading-backup', body_start)]

    assert "python scripts/" not in body, "備份信還在叫他跑 Python 腳本"
    assert "帳號" in body or "還原" in body, "沒有告訴他這個檔案要怎麼用"


def _email_body() -> str:
    from pathlib import Path

    from app.services import backup_schedule

    source = Path(backup_schedule.__file__).read_text(encoding="utf-8")
    start = source.index("附件是這個帳號的加密備份")
    return source[start : source.index('f"trading-backup', start)]


def test_the_email_does_not_promise_a_button_that_does_not_exist():
    """信裡叫他去按的東西，app 裡要真的有。

    上一條測試把「叫他跑 Python 腳本」擋掉了，而修法是改成「到 app 的『帳號』頁，備份
    那一區有『從備份還原』」——**那顆按鈕從來沒有被做出來。** 沒有還原端點，也沒有還原
    的畫面；整個 repo 裡只有策略「版本」的還原，那是另一件事。

    所以那一版把「一個他做不到的指示」換成「一個不存在的指示」，而後者更糟：前者至少
    對真的會用終端機的人是對的，後者對任何人都不成立。

    而這封信會定期寄出，收信的人不是工程師，讀到它的時機是他已經弄丟東西的那一天。

    這一條問的不是文案，是**那句承諾對得上程式碼**：信裡如果說 app 裡有還原，那就要有
    一條還原的路由。
    """
    from app.main import app

    body = _email_body()
    promises_a_control = "還原" in body and ("頁" in body or "按" in body)
    if not promises_a_control:
        return

    # 要遞迴走：FastAPI 把 include_router 進來的東西包在 `_IncludedRouter` 裡，所以
    # `app.routes` 第一層看到的不是路由本身（scripts/audit.py 的 websocket_paths 也是
    # 為了同一件事才那樣寫的）。少了這一段，這裡會看到一份空清單然後說「沒有還原路
    # 由」——結論碰巧一樣，理由卻是錯的，而那種測試下一次就會騙人。
    def walk(routes, seen=None):
        seen = set() if seen is None else seen
        for route in routes or []:
            if id(route) in seen:
                continue
            seen.add(id(route))
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(getattr(inner, "routes", []), seen)
                continue
            sub = getattr(route, "routes", None)
            if sub:
                yield from walk(sub, seen)
                continue
            yield getattr(route, "path", "")

    paths = set(walk(app.routes))

    assert any("backup" in path and "restore" in path for path in paths), (
        "備份信告訴他到 app 裡按「從備份還原」，而那條路由不存在。"
        f"現在有的備份相關路由：{sorted(p for p in paths if 'backup' in p)}"
    )
