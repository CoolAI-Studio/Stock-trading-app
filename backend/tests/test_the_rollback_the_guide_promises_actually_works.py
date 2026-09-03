"""退版那條路，是他在最糟的時刻照著做的——所以它必須真的走得通。

DEPLOYMENT.md 第 8 節「情況 B：程式碼壞了，而且這一版帶了遷移」給的是一句指令和一
個承諾：

    DATABASE_URL="postgresql://..." python -m alembic downgrade -1

    「每一支遷移都寫了 `downgrade()`，所以這是可行的」

那個承諾是一句**關於程式碼的事實宣稱**，而在這個檔案出現之前，沒有任何東西驗證
它。alembic 的 autogenerate 產出的 `downgrade()` 有時候是空的，手寫的也可能忘記；
兩種都不會有任何東西變紅，而他發現的時機是：線上已經壞了、他照著文件做、然後那句
指令什麼都沒做或者直接炸掉。

＊ 為什麼不是「反正很少用到」。

它很少用到，正是它危險的原因。這條路只有在**已經出事**的時候才會被走，而那時候他
沒有時間、也沒有第二個方案——文件告訴他先降級再回舊版，他手上就只有這一條。

＊ 三件事分開驗。

一、每一支遷移都真的有 downgrade（文件宣稱的那件事）。
二、文件給的那一句指令，在**有資料**的資料庫上跑得動。空的資料庫降級永遠會過，
    而他的資料庫不是空的——跟 test_an_update_lands_on_a_database_that_already_has_data
    是同一個理由。
三、降完之後升得回去。修好的版本上線時他還要再升一次，那一步不能是死路。
"""

import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from alembic import command
from tests.test_an_update_lands_on_a_database_that_already_has_data import _fill_every_table

BACKEND_ROOT = Path(__file__).resolve().parent.parent
VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _downgrade_body(source: str) -> list[str] | None:
    """`downgrade()` 裡面真的做事的那幾行，沒有這個函式就回 None。"""
    found = re.search(r"def downgrade\(\)[^:]*:\n(.*?)(?=\n(?:def |\Z))", source, re.DOTALL)
    if not found:
        return None
    lines = [line.strip() for line in found.group(1).split("\n") if line.strip()]
    return [
        line
        for line in lines
        if not line.startswith("#") and not line.startswith('"""') and line not in {"pass", "..."}
    ]


@pytest.mark.parametrize("path", sorted(VERSIONS.glob("*.py")), ids=lambda p: p.stem[:12])
def test_every_migration_can_be_undone(path: Path):
    """DEPLOYMENT.md 說「每一支遷移都寫了 downgrade()」——那句話要是真的。

    一支空的 downgrade 不會讓任何東西變紅，而它會在他線上已經壞掉、照著文件做的那一
    刻才現形。
    """
    body = _downgrade_body(path.read_text(encoding="utf-8"))

    assert body is not None, f"{path.name} 沒有 downgrade()"
    assert body, (
        f"{path.name} 的 downgrade() 是空的。DEPLOYMENT.md 第 8 節叫他在出事的時候跑 "
        "`alembic downgrade -1`，而那一句對這一版什麼都不會做。"
    )


def test_the_documented_downgrade_command_works_on_a_database_with_data(tmp_path, monkeypatch):
    """文件給的那一句 `downgrade -1`，在有資料的資料庫上真的跑得動。

    空的資料庫降級永遠會過（沒有東西要動），而他的資料庫不是空的。
    """
    db_path = tmp_path / "rollback.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    # alembic/env.py 讀的是 app.db.session.engine，不是 sqlalchemy.url。
    monkeypatch.setattr("app.db.session.engine", engine)
    cfg = _config()

    command.upgrade(cfg, "head")
    filled = _fill_every_table(engine)
    assert filled, "一張表都塞不進去，這一條就退化成「空資料庫降級」了"

    command.downgrade(cfg, "-1")

    # 表還在。降級掉一個欄位是預期的（文件自己也提醒要先備份），把整個資料庫清掉不是。
    after = inspect(engine).get_table_names()
    assert "users" in after, "退一版就把 users 弄不見了"
    engine.dispose()


def test_and_he_can_come_back_up_afterwards(tmp_path, monkeypatch):
    """降完之後升得回去——修好的版本上線時他還要再升一次。

    少了這一步，「先降級再回舊版」就是一條單行道：他退得下去，卻上不來。
    """
    db_path = tmp_path / "roundtrip.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr("app.db.session.engine", engine)
    cfg = _config()

    command.upgrade(cfg, "head")
    assert _fill_every_table(engine)
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    script = ScriptDirectory.from_config(cfg)
    with engine.connect() as connection:
        stamped = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    assert stamped == script.get_current_head(), "升回來之後停在的不是最新版"
    engine.dispose()
