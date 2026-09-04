"""還原這件事要有一顆按得到的按鈕，而那顆按鈕不可以是陌生人按得到的。

備份檔一直都做得出來（設定頁按一次、或每天自動寄到他信箱），但**倒回去的路只存在於
文件裡**——而那條路的第一句話是「在你的電腦上跑 psql」，對這個產品的使用者等於流程到
此結束（CLAUDE.md：任何「請在你的電腦上跑這支腳本」的指示，對這個使用者等於沒有）。

所以還原要是一個 HTTP 端點加一顆按鈕。而它一旦存在，就多了三件要守的事：

一、**要登入**。裡面有他的通知 token 和整份交易紀錄。
二、**要有大小上限**。備份是一份 JSON，一個人的交易紀錄不會有幾十 MB；沒有上限的話，
    這支端點就是一個把記憶體吃光的方法——而這台機器上跑著他所有的提醒。
三、**錯誤要說得出下一步**。密碼打錯、檔案選錯、版本太新，是三種他改得動的狀況，而
    「還原失敗」三個字對哪一種都沒有幫助。
"""

from decimal import Decimal

import pytest

from app.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import backup


@pytest.fixture
def his_backup(db_session, auth_client) -> bytes:
    """一份真的備份檔，裡面有一支策略。"""
    user = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    db_session.add(
        Strategy(
            user_id=user.id,
            name="備份裡的那一支",
            symbol="2330.TW",
            data_source=DataSource.YFINANCE,
            source_code="class Strategy:\n    def on_tick(self, price):\n        return 'HOLD'\n",
            code_hash="h",
            is_active=True,
            stop_loss_pct=Decimal("4.5"),
        )
    )
    db_session.commit()
    blob = backup.create(db_session, user, "correct-horse")
    db_session.query(Strategy).filter(Strategy.user_id == user.id).delete()
    db_session.commit()
    return blob


def _post(client, blob: bytes, passphrase: str = "correct-horse"):
    return client.post(
        "/api/backup/restore",
        files={"file": ("trading-backup.bak", blob, "application/octet-stream")},
        data={"passphrase": passphrase},
    )


def test_a_stranger_cannot_restore_anything(client):
    """裡面有他的通知 token 和整份交易紀錄，而這個端點會把東西寫進資料庫。

    刻意不用 `his_backup`：那個 fixture 會把 `auth_client` 拉進來，而它是在**同一個**
    client 上掛 token 的——用了就等於自己先登入了，這條測試會變成假綠燈。憑證的檢查排
    在讀檔之前，所以隨便一串位元組就問得出答案。
    """
    assert _post(client, b"does not matter").status_code == 401


def test_the_owner_can_and_gets_his_strategy_back(auth_client, db_session, his_backup):
    response = _post(auth_client, his_backup)

    assert response.status_code == 200, response.text
    assert response.json()["strategies"] == 1
    restored = db_session.query(Strategy).all()
    assert len(restored) == 1
    assert restored[0].stop_loss_pct == Decimal("4.5")


def test_the_answer_says_what_is_still_switched_off(auth_client, his_backup):
    """畫面要能說出「策略是停用的，等你打開」——不然他會以為提醒已經在跑了。"""
    body = _post(auth_client, his_backup).json()

    assert body["strategies"] == 1
    assert set(body) >= {"strategies", "channels", "positions", "expired_pending"}


def test_a_wrong_passphrase_says_so_instead_of_failing_vaguely(auth_client, his_backup):
    """他最可能弄錯的就是這個，而「還原失敗」不會讓他想到去翻密碼。"""
    response = _post(auth_client, his_backup, passphrase="wrong-passphrase")

    assert response.status_code == 422
    assert "密碼" in response.json()["detail"]


def test_a_file_that_is_not_a_backup_says_so(auth_client):
    """他會選到 .csv、選到那封信本身、選到一張圖。"""
    response = _post(auth_client, b"this is not a backup at all")

    assert response.status_code == 422
    assert "備份" in response.json()["detail"]


def test_nothing_is_written_when_the_file_cannot_be_read(auth_client, db_session, his_backup):
    """讀不懂就一個字都不要寫。半套的還原比沒有還原糟。"""
    before = db_session.query(Strategy).count()

    _post(auth_client, his_backup, passphrase="wrong-passphrase")

    assert db_session.query(Strategy).count() == before


def test_an_enormous_file_is_refused_before_it_is_read(auth_client):
    """沒有上限的話，這支需要登入的端點就是一個把記憶體吃光的方法——而這台機器上跑著
    他所有的提醒。
    """
    from app.api.routers.backup import _MAX_RESTORE_BYTES

    response = _post(auth_client, b"x" * (_MAX_RESTORE_BYTES + 10))

    assert response.status_code == 413


def test_it_is_a_post_so_the_passphrase_never_lands_in_a_url():
    """密碼不可以進網址、伺服器 log 或瀏覽紀錄——跟下載備份那一支同一條規則。

    問路由表而不是打一個 GET：`/api` 底下打不到的路徑會被 main 的 catch-all 判成 404，
    所以「回 404」對「這條路由是不是只收 POST」什麼都證明不了。
    """
    from app.api.routers.backup import router

    methods = {
        method
        for route in router.routes
        if getattr(route, "path", "") == "/backup/restore"
        for method in route.methods
    }

    assert methods == {"POST"}, methods
