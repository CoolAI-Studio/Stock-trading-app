"""一次跑不動的遷移，不可以把一份**已經在跑**的部署變成一張設定頁。

＊ 這是同一條規則的第三種形狀（#50）。

CLAUDE.md 已經釘住兩條：新的必填設定會讓別人的實例停下來（`_REQUIRED_SECRETS`），
編譯失敗要先問「它在上一個版本編得過嗎」（`_record_compile_failure`）。這是第三條，
而它的入口比那兩條都早——早到 app 還沒開始跑。

＊ 現在的形狀。

`scripts/start.py` 開機跑 `alembic upgrade head`。跑不動（新的遷移在他的資料上出錯、
容器起來那一刻資料庫剛好抖一下、或 180 秒逾時）就把理由寫進 `DATABASE_MIGRATION_ERROR`；
`app/main.py` 的 `boot_problem()` 讀它，`setup_mode_active()` 因此是 True，於是
lifespan 裡的 `run_worker` 是 False——**worker 一次都不跑，他所有的提醒停擺**，而且
行程活著就不會自己復原。他要自己發現、自己去重新部署，而畫面上只寫著「這個部署還沒
設定完成」。

那句話對**第一次**部署的人是對的：他把 DATABASE_URL 貼錯了，設定頁是他唯一需要的
東西，而平台的健康檢查指著 `/healthz`，探測不過就是「部署失敗」，那一頁會跟著消失。
對一個已經跑了三個月的人是錯的。兩者長得一模一樣，因為程式碼裡沒有任何一處問過
**「這個資料庫已經有帳號了嗎」**。

＊ 為什麼判準是「有沒有帳號」。

它是「這份部署以前成功跑起來過」唯一問得到的證據，而且同時就是「有沒有東西可以失
去」的判準：沒有帳號就沒有策略、沒有提醒，鎖住不會讓任何一則通知消失。

問不到（連不上）算「沒有」：連不上正是第一次部署最常見的樣子，而那時候鎖住是對的。
"""

import os
from types import SimpleNamespace

import pytest

from scripts import start

_A_FAILED_MIGRATION = "alembic exited with 1"


@pytest.fixture(autouse=True)
def _a_clean_boot(monkeypatch):
    """這兩個環境變數是 start.main() 自己寫的，所以要在測試之間清乾淨。

    用 `setenv("")` 而不是 `delenv`：monkeypatch 只還原它記錄過的東西，而 delenv 一
    個本來就不存在的名字**不會留下記錄**——那樣 main() 寫進去的值會漏到下一條測試身
    上。空字串在兩個讀取端都等於「沒有」（`.strip() or None`）。
    """
    monkeypatch.setenv("DATABASE_MIGRATION_ERROR", "")
    monkeypatch.setenv("DATABASE_MIGRATION_STALE", "")


@pytest.fixture(autouse=True)
def _do_not_actually_start_uvicorn(monkeypatch):
    """main() 的最後一步是把自己換成 uvicorn；這裡要看的是它**在那之前**做的決定。

    兩條路都要擋，不是二選一：POSIX 走 `os.execv`，Windows 走 `subprocess.run`，而
    線上是 Linux、開發機是 Windows。只擋一條就會變成本機綠、CI 紅。
    """
    monkeypatch.setattr(start.os, "execv", lambda *args: None)
    monkeypatch.setattr(
        start.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )


@pytest.fixture(autouse=True)
def _the_migration_fails(monkeypatch):
    """這個檔案裡每一條的前提都是「遷移跑不動」。差別只在那是誰的資料庫。"""
    monkeypatch.setattr(start, "run_migrations", lambda: _A_FAILED_MIGRATION)
    # 探測之間的等待對測試只是秒數，沒有意義。raising=False 是為了讓這個檔案在修好
    # 之前也跑得起來——那時候它要為了「app 被鎖住」而紅，不是為了 AttributeError。
    monkeypatch.setattr(start, "_PROBE_GAP_SEC", 0, raising=False)


def _his_database_with_one_account(tmp_path, monkeypatch) -> None:
    """一份**已經在用**的資料庫：schema 在，而且裡面有一個帳號。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import app.models  # noqa: F401 -- 讓每一張表都登記進 Base.metadata
    from app.db.base import Base
    from app.models.user import User

    url = f"sqlite:///{(tmp_path / 'his.db').as_posix()}"
    engine = create_engine(url)
    try:
        Base.metadata.create_all(bind=engine)
        with Session(engine) as session:
            session.add(User(email="he@example.com", hashed_password="not-a-real-hash"))
            session.commit()
    finally:
        engine.dispose()
    monkeypatch.setattr("app.config.settings.DATABASE_URL", url)


def _a_brand_new_empty_database(tmp_path, monkeypatch) -> None:
    """第一次部署：連得上，但裡面什麼都沒有——遷移就是在這時候跑不動的。"""
    monkeypatch.setattr(
        "app.config.settings.DATABASE_URL", f"sqlite:///{(tmp_path / 'blank.db').as_posix()}"
    )


def test_a_deployment_that_already_has_accounts_is_not_locked_into_setup_mode(
    tmp_path, monkeypatch
):
    """核心。已經有帳號＝這份部署以前跑得起來，那麼遷移失敗是我們這次改動的事，不是
    他還沒設定完。鎖住的代價是他每一則提醒都不會再送出去，而且不會自己好。"""
    from app import main

    _his_database_with_one_account(tmp_path, monkeypatch)

    start.main()

    assert main.boot_problem() is None
    assert main.setup_mode_active() is False


def test_and_the_api_that_his_screen_asks_is_not_locked_either(tmp_path, monkeypatch, client):
    """設定模式把每一條 API 換成 503 ＋ `setup_required`。

    測到這一層，因為「被鎖住」對他來說不是一個旗標，是畫面上每一個問題都得不到答案
    ——包括「我的策略還在嗎」。
    """
    _his_database_with_one_account(tmp_path, monkeypatch)

    start.main()

    response = client.get("/api/strategies")

    assert response.status_code != 503
    assert response.json().get("setup_required") is None


def test_a_first_deploy_that_never_worked_still_gets_the_setup_page(tmp_path, monkeypatch):
    """反過來那一邊一樣不可以退。

    第一次部署的人多半是 DATABASE_URL 貼錯，設定頁是他唯一需要的東西；而平台的健康
    檢查指著 /healthz，探測不過就是「部署失敗」，那一頁會跟著一起消失。
    """
    from app import main

    _a_brand_new_empty_database(tmp_path, monkeypatch)

    start.main()

    assert main.boot_problem() == _A_FAILED_MIGRATION
    assert main.setup_mode_active() is True


def test_a_database_nobody_can_reach_counts_as_a_first_deploy(monkeypatch):
    """「問不到」不可以當成「它以前跑起來過」。

    連不上正是第一次部署最常見的樣子——DATABASE_URL 是整張表單上唯一一個 app 生不出
    來、只能去別人家的服務複製貼上的值，也就是最可能貼錯的那一格。
    """
    from app import main

    monkeypatch.setattr("app.config.settings.DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nope")

    start.main()

    assert main.setup_mode_active() is True


def test_the_reason_is_not_thrown_away_just_because_it_no_longer_locks(tmp_path, monkeypatch):
    """不鎖，不等於當作沒發生。

    schema 可能停在舊版，而那要有人看得到。這條先確保理由沒有被吞掉——把它顯示在哪
    一頁是另一張票，但一個沒有被記下來的失敗，之後沒有任何一頁救得回來。
    """
    _his_database_with_one_account(tmp_path, monkeypatch)

    start.main()

    assert os.environ["DATABASE_MIGRATION_STALE"] == _A_FAILED_MIGRATION
