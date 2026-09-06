"""我們每一次 push，都是別人機器上的一次升級——而升級這件事沒有人排練過。

CLAUDE.md 第一優先：「更新不可以停掉已經在跑的那一份。」而在這個檔案出現之前，CI 驗
的是「**從空的**建起來」：

    遷移檢查      `alembic upgrade head` 從一個空的 SQLite 檔開始
    first-deploy  全空設定的容器，SQLite，第一次啟動

真實事件是另一件事：**他的資料庫裡已經有東西，而且是 Postgres。** 那條路上有兩件我們
從來沒跑過的事：

1. **Postgres。** CI 裡出現 postgres 的唯一一處，是一個故意連不通的假位址（用來測失敗
   路徑）。遷移鏈真正執行的地方只有使用者那台——而
   `test_a_postgres_only_migration_is_not_unverified` 的檔頭已經寫著，帶 dialect 守衛
   的那幾支遷移「語法那一半沒有人驗過」。
2. **既有資料。** 一支把欄位改名、或忘記給既有列預設值的遷移，在空資料庫上永遠是綠
   的，在他那台是 `alembic upgrade head` 非零退出——而映像檔的 CMD 是
   `alembic upgrade head && uvicorn`，所以 uvicorn 從來不會被執行。

失敗的形狀是這個 repo 最怕的那一種：**我們全綠，他的服務起不來，而提醒全面停擺。**他
不會收到任何東西告訴他這件事，因為會告訴他的那個東西也在那個容器裡。

所以這一關把那件事排練一遍：起一個真的 Postgres，用**上一個released 版本**（`stable`，
也就是他現在正在跑的那一版）開起來、建資料，然後換成這一個 commit 的映像檔指同一個資料
庫，看它起不起得來、資料還在不在。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "backend" / "scripts" / "upgrade_smoke.py"


@pytest.fixture
def without_comments() -> str:
    """註解裡提到什麼都不算數（這個 repo 已經因此誤判過一次）。"""
    return "\n".join(
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_there_is_a_gate_that_rehearses_the_upgrade(without_comments: str):
    """有這一關。沒有的話，第一個跑到這條路的人就是使用者本人。"""
    assert "\n  upgrade:" in without_comments


def test_it_runs_against_the_database_he_actually_has(without_comments: str):
    """Postgres，不是 SQLite。

    這一關存在的一半理由就是它：帶 dialect 守衛的遷移在 SQLite 上整支被跳過，所以那些
    手寫的 `ALTER TABLE … TYPE TIMESTAMP WITH TIME ZONE` 從來沒有被執行過。
    """
    upgrade_job = without_comments.split("\n  upgrade:", 1)[1]

    assert "services:" in upgrade_job
    assert "postgres" in upgrade_job.split("services:", 1)[1][:600]


def test_it_starts_from_the_version_he_is_running_now(without_comments: str):
    """從 `stable` 升上來，不是從空的建起來。

    `stable` 就是他現在那一份（見 CLAUDE.md #52：後端追 stable ＋ autoDeploy），所以
    這一關演的正是他下一次會遇到的那一步。
    """
    upgrade_job = without_comments.split("\n  upgrade:", 1)[1]

    assert "stable" in upgrade_job


def test_a_broken_upgrade_does_not_ship(without_comments: str):
    """這一關紅了就不可以部署。

    圖表和 first-run 刻意**不**擋部署（警告不能停擺優先於畫面）。這一關相反：它紅的意
    思是「送出去會讓已經在跑的那一份起不來」，而那正是提醒全面停擺本身。
    """
    deploy_job = without_comments.split("\n  deploy:", 1)[1]
    needs = deploy_job.split("needs:", 1)[1].split("\n", 1)[0]

    assert "upgrade" in needs


def test_both_boots_share_one_encryption_key(without_comments: str):
    """兩次啟動要用同一把 SECRET_ENCRYPTION_KEY。

    通知管道的設定是用它加密存的（EncryptedJSON）。兩次用不同的金鑰，升級之後那些管道
    一律解不開——而那是「提醒送不出去」最安靜的一種。用同一把，這一關才驗得到「舊資料
    在新版讀得回來」。
    """
    upgrade_job = without_comments.split("\n  upgrade:", 1)[1]
    lines = [line.strip() for line in upgrade_job.splitlines() if "SECRET_ENCRYPTION_KEY" in line]

    # 值只寫死一次（job 層的 env），兩次啟動都是**引用**它。這樣兩邊不可能各自漂走，
    # 而「兩次用同一把」就不再是靠人記得。
    literal = [line for line in lines if line.startswith("SECRET_ENCRYPTION_KEY:")]
    passed_in = [line for line in lines if line.startswith("-e SECRET_ENCRYPTION_KEY=")]

    assert len(literal) == 1, f"金鑰的值應該只寫死一次，看到 {len(literal)} 次"
    assert len(passed_in) >= 2, "兩次啟動都要帶這把金鑰"
    assert all("$SECRET_ENCRYPTION_KEY" in line for line in passed_in), (
        "有一次啟動帶的是自己寫死的值，兩邊會漂走"
    )


def test_the_rehearsal_can_both_seed_and_check(without_comments: str):
    """腳本要有「種資料」和「驗資料還在」兩半。

    只有前一半的話，這一關證明的只是「新版起得來」——而升級真正會弄丟的東西是資料。
    """
    assert SCRIPT.exists(), "缺 scripts/upgrade_smoke.py"
    source = SCRIPT.read_text(encoding="utf-8")

    assert "def seed(" in source
    assert "def verify(" in source
    assert "scripts/upgrade_smoke.py" in without_comments


def test_the_seed_uses_payloads_this_app_actually_accepts(auth_client):
    """排練種下去的東西，要真的建得起來。

    這一條在本機用 TestClient 跑，為的是不要讓一個打錯的欄位名等到 CI 上、等到兩個映像
    檔都建完、兩個容器都起來之後才現形——那一輪要十幾分鐘，而錯誤會出現在一個沒有人會
    展開的步驟裡。

    它**不能**取代那一關：這裡是 SQLite、同一個行程、沒有遷移、沒有跨版本。這裡只證明
    「請求的形狀是對的」。
    """
    from scripts import upgrade_smoke

    added = auth_client.post("/api/watchlist", json={"symbol": upgrade_smoke.WATCHED_SYMBOL})
    assert added.status_code in (200, 201), added.text

    channel = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "telegram",
            "label": upgrade_smoke.CHANNEL_LABEL,
            "config": {
                "bot_token": upgrade_smoke.BOT_TOKEN,
                "chat_id": upgrade_smoke.CHAT_ID,
            },
        },
    )
    assert channel.status_code == 201, channel.text
    # 加密的設定讀得回來——排練驗的就是這一格，所以這裡先確認它本來就讀得回來。
    assert upgrade_smoke.CHAT_ID in channel.json()["config_preview"]

    strategy = auth_client.post(
        "/api/strategies",
        json={
            "name": upgrade_smoke.STRATEGY_NAME,
            "symbol": upgrade_smoke.WATCHED_SYMBOL,
            "source_code": upgrade_smoke.STRATEGY_SOURCE,
            "warmup_bars": 1,
        },
    )
    assert strategy.status_code == 201, strategy.text

    # 新建的策略是停用的，而排練要留下一支**啟用中**的——否則「升級之後策略還是啟用中」
    # 那條斷言永遠是真的，等於沒有驗。
    activated = auth_client.post(f"/api/strategies/{strategy.json()['id']}/activate")

    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True
