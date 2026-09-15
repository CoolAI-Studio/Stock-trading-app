"""收盤摘要的開關也在備份裡（#117）。

備份檔是對外契約（CLAUDE.md #81）：加一張表就要 `_snapshot` 和 `restore` 一起改，否則症狀是
靜默的——備份看起來完整，倒回去卻少了東西。

還原的語意照風控設定那一條：一個帳號一份，**只在完全沒有的時候才建**，絕不蓋掉他現在那一份。
「上一次送出是哪一天」不帶過去：那是這一份部署的執行紀錄，不是他的設定——帶過去的話，換一份
部署的第一天會以為今天已經送過了。
"""

import pytest

from app.models.daily_summary import DailySummary
from app.models.user import User
from app.services import backup


@pytest.fixture
def owner(db_session) -> User:
    user = User(email="owner@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def other(db_session) -> User:
    user = User(email="somebody-else@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _snapshot_of(db, user: User) -> dict:
    return backup.read(backup.create(db, user, "correct-horse"), "correct-horse")


def test_the_switch_is_in_the_backup(db_session, owner):
    db_session.add(DailySummary(user_id=owner.id, is_enabled=True))
    db_session.commit()

    snapshot = _snapshot_of(db_session, owner)

    assert snapshot["daily_summary"] == [{"is_enabled": True}]


def test_it_comes_back_when_he_has_none(db_session, owner, other):
    db_session.add(DailySummary(user_id=other.id, is_enabled=True))
    db_session.commit()
    snapshot = _snapshot_of(db_session, other)

    report = backup.restore(db_session, owner, snapshot)

    mine = db_session.query(DailySummary).filter(DailySummary.user_id == owner.id).one()
    assert mine.is_enabled is True
    assert mine.tw_done_on is None and mine.us_done_on is None
    assert report.daily_summary_created is True


def test_his_current_setting_is_not_overwritten(db_session, owner, other):
    db_session.add(DailySummary(user_id=other.id, is_enabled=True))
    db_session.add(DailySummary(user_id=owner.id, is_enabled=False))
    db_session.commit()
    snapshot = _snapshot_of(db_session, other)

    report = backup.restore(db_session, owner, snapshot)

    mine = db_session.query(DailySummary).filter(DailySummary.user_id == owner.id).one()
    assert mine.is_enabled is False
    assert report.daily_summary_created is False


def test_a_backup_from_before_the_summary_existed_is_accepted(db_session, owner, other):
    snapshot = _snapshot_of(db_session, other)
    snapshot.pop("daily_summary", None)

    backup.restore(db_session, owner, snapshot)

    assert db_session.query(DailySummary).filter(DailySummary.user_id == owner.id).count() == 0
