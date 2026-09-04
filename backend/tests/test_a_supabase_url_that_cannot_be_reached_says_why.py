"""「連不上資料庫」對這條路上最常見的那個錯，什麼都沒說。

＊ 這條路是我們自己推薦的，所以它踩到的坑是我們的責任。

資料庫那一頁現在把 Supabase 標成推薦（Neon 的每月運算時數會在月中用完，#95）。而
Supabase 的主控台預設給的那一串是 **Direct connection**，它在免費方案上**只有 IPv6
位址**：

    「Direct connections are on IPv6, or on IPv4 if the project has the IPv4
     add-on.」（Supabase 文件，2026-09）

而雲端平台的容器出去是 IPv4。所以照著「複製 Connection string」做完的人，拿到的是一
串**這台機器永遠連不到**的位址——而畫面上只會寫「資料庫連不上，對方回的是：連線逾
時」，那句話對他來說跟「密碼打錯了」「資料庫被刪了」長得一模一樣。

他會做的事是回去重新複製一次那一串。而那一串每次都一樣。

＊ 正確答案是同一頁上的另一個分頁。

**Session pooler**（`aws-0-<區域>.pooler.supabase.com:5432`）是 IPv4，行為跟直連一
樣。Supabase 自己也是這樣寫的：「Use pooler session mode for application traffic
from persistent clients on IPv4-only networks.」

（旁邊那個 Transaction pooler（6543）也是 IPv4，但它「does not support prepared
statements」，而 SQLAlchemy ＋ psycopg2 預設會用——所以不是它。）

＊ 這一格是 app 講得出來的。

它手上就有那一串。`db.<專案>.supabase.co` 這個形狀是直連，一眼就認得出來——所以這不
是「叫他去別的地方查」，是「告訴他手上那個值錯在哪裡」。CLAUDE.md 那條規則的正面。
"""

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.services import setup_state

DIRECT = "postgresql://postgres:pw@db.abcdefghijklm.supabase.co:5432/postgres"
SESSION_POOLER = (
    "postgresql://postgres.abcdefghijklm:pw@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"
)
SOMEWHERE_ELSE = "postgresql://user:pw@ep-cool-name.ap-southeast-1.aws.neon.tech/db"


@pytest.fixture(autouse=True)
def _it_did_not_come_up(monkeypatch):
    """開機時連不上——這一格的說明只有在那個狀態下才會出現。"""
    monkeypatch.setenv("DATABASE_MIGRATION_ERROR", "connection timed out")


def _advice(url: str) -> str:
    settings = Settings(
        DATABASE_URL=url,
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        SECRET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    row = next(
        item for item in setup_state.missing_settings(settings) if item.name == "DATABASE_URL"
    )
    return f"{row.why}\n{row.how}"


def test_a_direct_supabase_url_gets_told_which_tab_to_use():
    """重新複製一次同一串沒有用。要說的是「換那一頁上的另一個分頁」。"""
    advice = _advice(DIRECT)

    assert "Session pooler" in advice, advice


def test_it_says_why_so_he_does_not_think_it_is_a_typo():
    """少了原因，那句話會變成又一個「照做看看」的指示。"""
    advice = _advice(DIRECT)

    assert "IPv6" in advice or "IPv4" in advice, advice


def test_a_pooler_url_is_not_told_to_switch_to_a_pooler():
    """他已經照做了。再叫他做一次，他會以為自己弄錯了，然後去改一個對的東西。"""
    assert "Session pooler" not in _advice(SESSION_POOLER)


def test_somebody_else_s_database_does_not_get_supabase_advice():
    """這句話只對 Supabase 成立。對 Neon 講會把他送去一個不存在的分頁。"""
    assert "Session pooler" not in _advice(SOMEWHERE_ELSE)


def test_the_advice_that_was_already_there_is_not_lost():
    """「多半是那一串本身有問題」對其他每一種情況仍然是對的。"""
    advice = _advice(DIRECT)

    assert "重新複製" in advice or "連線字串" in advice, advice
