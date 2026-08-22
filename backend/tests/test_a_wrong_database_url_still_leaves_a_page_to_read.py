"""貼錯一個字的連線字串，不能讓整個部署變成一個死掉的網址。

`DATABASE_URL` 是部署表單上唯一一格「app 生不出來、只能自己去別人家的服務複製
貼上」的值——也就是最可能貼錯的那一格。而容器的啟動指令是

    alembic upgrade head && uvicorn ...

連不上資料庫，alembic 非零退出，**uvicorn 根本不會啟動，連埠都不會綁**。於是
「行程活著、告訴你缺什麼」那一整套在最需要它的時候完全用不到：`setup_state.py`
把 DATABASE_URL 列成第 1 步、還寫好了要對使用者說的話，而那段話只有在行程活著
的時候送得出去。

兩件事分開講，因為使用者要做的事不一樣：

    還沒填     → 去開一個資料庫，把連線字串貼進來
    填了連不上 → 那一串有問題（打錯、密碼換了、資料庫被刪了）

而「連不上」的理由**不能帶密碼**：驅動的錯誤訊息會把整串 DSN 原封不動印出來，
而這一段是要顯示在畫面上的。
"""

import pytest

from app.config import Settings
from app.services import setup_state

CLOUD_MARKERS = ("RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO", "KOYEB_APP_NAME")
LEAKY_DSN = "postgresql://trader:hunter2secret@127.0.0.1:1/nope"


@pytest.fixture(autouse=True)
def _nowhere_in_particular(monkeypatch):
    for marker in CLOUD_MARKERS:
        monkeypatch.delenv(marker, raising=False)


def _settings(url: str) -> Settings:
    from cryptography.fernet import Fernet

    return Settings(
        DATABASE_URL=url,
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        SECRET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )


def _database_row(settings: Settings):
    return next(
        (item for item in setup_state.missing_settings(settings) if item.name == "DATABASE_URL"),
        None,
    )


def test_an_empty_url_reads_as_not_filled_in_yet(monkeypatch):
    monkeypatch.delenv("DATABASE_MIGRATION_ERROR", raising=False)
    row = _database_row(_settings(""))

    assert row is not None
    assert row.blocking is True
    assert "連不上" not in row.why


def test_a_url_that_cannot_be_reached_says_so_instead(monkeypatch):
    """跟「還沒填」不同的一句話，因為要做的事不一樣。

    理由是**開機時**那一次遷移留下來的（scripts/start.py 記在環境變數裡），不是
    現在再連一次——現在連一次的版本會讓一份已經設定好的部署因為資料庫抖一下就
    重新變成「未設定」，也就是把不需要登入的設定端點重新打開。
    """
    monkeypatch.setenv(
        "DATABASE_MIGRATION_ERROR",
        "OperationalError: connection to server at postgresql://***@127.0.0.1 failed",
    )
    row = _database_row(_settings(LEAKY_DSN))

    assert row is not None
    assert row.blocking is True
    assert "連不上" in row.why


def test_and_that_sentence_never_carries_the_password(monkeypatch):
    """驅動的錯誤訊息會把整串 DSN 印出來，而這一段是要顯示在畫面上的。
    洗掉密碼是在 scripts/start.py 存進去之前就做完的，所以這裡連拿都拿不到。"""
    from scripts.start import scrub

    monkeypatch.setenv(
        "DATABASE_MIGRATION_ERROR",
        scrub(f"OperationalError: could not connect to {LEAKY_DSN}"),
    )
    row = _database_row(_settings(LEAKY_DSN))

    assert row is not None
    text = f"{row.why} {row.how}"
    assert "hunter2secret" not in text
    assert LEAKY_DSN not in text


def test_a_database_that_works_is_not_reported_as_unreachable(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_MIGRATION_ERROR", raising=False)
    row = _database_row(_settings(f"sqlite:///{(tmp_path / 'ok.db').as_posix()}"))

    assert row is None or "連不上" not in row.why


# --- 啟動流程本身 ---------------------------------------------------------------------


def test_a_failed_migration_does_not_stop_the_server_from_starting(monkeypatch):
    """這是這張票的核心：遷移跑不動的時候，服務仍然要起得來，因為那正是使用者
    需要讀到設定頁的時刻。"""
    from scripts import start

    monkeypatch.setenv("DATABASE_URL", LEAKY_DSN)

    problem = start.run_migrations()

    assert problem is not None  # 回報，而不是讓行程結束


def test_the_migration_failure_is_reported_without_the_password(monkeypatch):
    from scripts import start

    monkeypatch.setenv("DATABASE_URL", LEAKY_DSN)

    problem = start.run_migrations()

    assert "hunter2secret" not in (problem or "")


def test_a_working_database_still_gets_its_migrations(monkeypatch, tmp_path):
    """把遷移改成不擋啟動，不可以順手變成不跑：一個正常的部署仍然要在開機時
    把 schema 帶到最新。"""
    from scripts import start

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")

    problem = start.run_migrations()

    assert problem is None
    assert (tmp_path / "fresh.db").exists()


# --- 第一次部署不能因此被判定失敗 -----------------------------------------------------


def test_a_boot_migration_failure_counts_as_still_being_set_up(monkeypatch):
    """部署平台的健康檢查指著 `/healthz`，而第一次部署沒有可以退回的舊版本——
    探測不過就是「部署失敗」，於是那個要解釋問題的頁面跟著一起消失，正好死在它
    是唯一有用的東西的時候。

    開機時遷移就跑不動，代表這份部署從來沒有起來過（schema 可能根本不存在）。
    那是「還在設定」，不是「一個正常運作的系統壞了」——後者仍然要回 503，因為
    看門狗靠它。
    """
    monkeypatch.setenv("DATABASE_MIGRATION_ERROR", "OperationalError: connection refused")

    from app import main

    assert main.boot_problem() is not None


def test_and_a_healthy_boot_has_no_such_problem(monkeypatch):
    monkeypatch.delenv("DATABASE_MIGRATION_ERROR", raising=False)

    from app import main

    assert main.boot_problem() is None


def test_the_probe_answers_200_while_that_is_true(client, monkeypatch):
    monkeypatch.setenv("DATABASE_MIGRATION_ERROR", "OperationalError: connection refused")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "setup"
