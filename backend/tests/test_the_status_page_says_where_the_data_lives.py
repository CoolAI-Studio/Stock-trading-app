"""這份部署的資料存在哪裡，畫面上要說得出來。

WHY IT IS NOT OBVIOUS TO THE OWNER. 他沒有打過那一格——`DATABASE_URL` 是在部署
平台的環境變數裡填的，可能是幾週前，也可能根本沒填（那樣就會落在預設的本機檔案
上）。而這兩種情況在每一個畫面上長得一模一樣：登入得進去、策略跑得動、儀表板有
數字。差別只在某一次重新部署之後，東西還在不在。

所以狀態頁直接說：是檔案還是 Postgres，以及——如果是檔案——它會不會在下一次
重新部署時消失。後面那個問題的答案取決於這個行程跑在哪裡，而 app 現在認得出來。

從來不說的是連線字串本身。裡面有密碼，而這一段是要顯示在畫面上的。
"""

import pytest

CLOUD_MARKERS = ("RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO", "KOYEB_APP_NAME")


@pytest.fixture(autouse=True)
def _nowhere_in_particular(monkeypatch):
    for marker in CLOUD_MARKERS:
        monkeypatch.delenv(marker, raising=False)


def test_it_says_what_kind_of_database_this_is(auth_client):
    body = auth_client.get("/api/system/status").json()

    assert body["database"]["kind"] in ("sqlite", "postgres", "other")


def test_a_file_database_on_a_cloud_platform_is_flagged_as_temporary(auth_client, monkeypatch):
    """The failure this exists to prevent: everything works, and then one
    redeploy later the account, the strategies and the notification channels
    are all gone, with nothing having warned anybody."""
    monkeypatch.setenv("RENDER", "true")

    body = auth_client.get("/api/system/status").json()

    assert body["database"]["kind"] == "sqlite"
    assert body["database"]["ephemeral"] is True
    assert "重新部署" in body["database"]["detail"]


def test_the_same_file_on_your_own_machine_is_not(auth_client):
    """在自己的機器上，那個檔案就在那裡。說它會消失是錯的。"""
    body = auth_client.get("/api/system/status").json()

    assert body["database"]["kind"] == "sqlite"
    assert body["database"]["ephemeral"] is False


def test_it_never_prints_the_connection_string(auth_client, monkeypatch):
    """密碼就在那串裡面，而這一段是要顯示在畫面上的。"""
    canary = "postgresql://trader:hunter2secret@ep-canary.example.com/trading"
    monkeypatch.setattr("app.config.settings.DATABASE_URL", canary)

    response = auth_client.get("/api/system/status")

    assert "hunter2secret" not in response.text
    assert canary not in response.text


def test_a_postgres_url_reads_as_postgres(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.DATABASE_URL", "postgresql://user:pw@db.example.com/trading"
    )

    body = auth_client.get("/api/system/status").json()

    assert body["database"]["kind"] == "postgres"
    assert body["database"]["ephemeral"] is False


def test_nobody_else_gets_to_read_it(client):
    """It says something about how this deployment is put together, and there
    is no reason for a stranger to know that."""
    assert client.get("/api/system/status").status_code == 401


# --- 「那一格要去哪裡填」--------------------------------------------------------------


def test_it_says_which_platform_this_is_running_on(auth_client, monkeypatch):
    """設定引導要講出「在你的平台上，那一頁叫什麼」。同一句話對 Render 和
    Fly.io 的使用者是不一樣的，而錯的那一句比含糊更糟——他會真的去找那一頁。"""
    monkeypatch.setenv("FLY_APP_NAME", "somebodys-app")

    body = auth_client.get("/api/system/status").json()

    assert body["platform"]["name"] == "Fly.io"
    assert "secrets" in body["platform"]["env_where"].lower()


def test_and_says_something_usable_when_it_cannot_tell(auth_client):
    body = auth_client.get("/api/system/status").json()

    assert body["platform"]["env_where"]
    for company in ("Render", "Heroku", "Railway", "Fly.io", "Koyeb"):
        assert company not in body["platform"]["env_where"]
