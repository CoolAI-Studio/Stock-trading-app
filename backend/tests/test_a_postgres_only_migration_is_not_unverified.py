"""只在 Postgres 上跑的遷移，是這條鏈上唯一沒有人看過的一段。

＊ 這一段程式碼在我們這邊從來不執行。

`f5d5b2ce6b3d` 開頭是這樣：

    if op.get_bind().dialect.name != "postgresql":
        return

那個守衛是對的——SQLite 的 DATETIME 沒有時區這個概念，那一版要做的事在它上面沒有意
義。但它的後果是：**CI（SQLite）和整個測試套件（SQLite）都不會執行到那一版的任何一
行。** 它唯一真正跑起來的地方，是使用者的 Postgres——也就是沒有人在看的那一台。

而它做的事情是手寫的原始 SQL，靠一份 (表, 欄) 清單逐條組字串：

    ALTER TABLE {table} ALTER COLUMN "{column}" TYPE TIMESTAMP WITH TIME ZONE …

清單裡只要有一個名字打錯、或者那一欄在後來的版本被改名了，`alembic upgrade head` 就
會在他那邊失敗，容器起不來——而我們這邊全綠。

＊ 沒有 Postgres 也驗得到的那一半。

那句 SQL 的正確性有兩層：**語法**（要有 Postgres 才驗得到）和**它指名的東西存不存
在**（不用）。第二層正是最會出錯的那一層——名字會過期，語法不會。

所以做法是：升到那一版的前一版，把 schema 反射出來，逐一問「這張表在嗎、這一欄在
嗎」。

＊ 而且不是只顧現在這一支。

哪一支遷移有 dialect 守衛是**掃出來的**，不是寫死的。將來多一支，這裡自動涵蓋；而如
果那一支的形狀讓這裡讀不出它要動哪幾欄，測試會**紅**而不是安靜跳過——一個「找不到就
算了」的檢查，跟沒有檢查是同一件事。
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parent.parent
VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def _dialect_guarded() -> list[Path]:
    """有 dialect 守衛的遷移——也就是在我們的 CI 上不會被執行的那些。"""
    return sorted(
        path for path in VERSIONS.glob("*.py") if "dialect.name" in path.read_text(encoding="utf-8")
    )


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_column_pairs(module) -> list[tuple[str, str]]:
    """模組層級那份「要動哪幾欄」的清單。

    找的是形狀（一串 (str, str)），不是某個寫死的名字：下一支這種遷移不會叫
    `_COLUMNS`，但它幾乎一定也要這樣列出它要動的東西。找不到的時候讓測試紅，見檔頭。
    """
    pairs: list[tuple[str, str]] = []
    for name, value in vars(module).items():
        if name.startswith("__") or not isinstance(value, tuple | list) or not value:
            continue
        if all(
            isinstance(item, tuple | list)
            and len(item) == 2
            and all(isinstance(part, str) for part in item)
            for item in value
        ):
            pairs.extend((item[0], item[1]) for item in value)
    return pairs


@pytest.mark.parametrize("path", _dialect_guarded(), ids=lambda p: p.stem[:12])
def test_a_postgres_only_migration_names_columns_that_exist(path: Path, tmp_path, monkeypatch):
    """它指名的每一張表、每一欄，在它跑的那一刻都要真的在。

    名字會過期（欄位改名、表被合併），而這一版在我們這邊一行都不會執行——所以過期了
    也不會有任何東西變紅，直到他的容器起不來。
    """
    module = _load(path)
    pairs = _table_column_pairs(module)

    assert pairs, (
        f"{path.name} 有 dialect 守衛，但這條測試讀不出它要動哪幾欄——"
        "那表示它在我們這邊完全沒有被驗證過。把它要動的東西列成模組層級的 (表, 欄) "
        "清單，或者把這條測試改成讀得懂它的樣子。"
    )

    db_path = tmp_path / "chain.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    # alembic/env.py 讀的是 app.db.session.engine，不是 sqlalchemy.url。
    monkeypatch.setattr("app.db.session.engine", engine)

    # 升到**它的前一版**：這一版要動的東西，必須在它自己跑起來的那一刻就已經存在。
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), module.down_revision)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table, column in pairs:
        if table not in tables:
            missing.append(f"{table}（整張表不在）")
            continue
        if column not in {col["name"] for col in inspector.get_columns(table)}:
            missing.append(f"{table}.{column}")

    assert not missing, (
        f"{path.name} 要改的這幾個在它跑的時候並不存在：{'、'.join(missing)}。"
        "在 Postgres 上這一句會直接失敗，而他的容器就起不來——我們這邊看不到，"
        "因為 SQLite 上這一版整段被守衛跳過。"
    )
    engine.dispose()


def test_the_guard_is_not_the_only_thing_standing_between_us_and_that_code():
    """至少要有人記得這種遷移存在。

    這一條是給下一個人看的：dialect 守衛是一個**正確**的東西，但它同時把那一段程式
    碼移出了所有自動檢查的範圍。這個檔案就是補那一塊，所以它必須跟著那種遷移一起長。
    """
    guarded = _dialect_guarded()

    assert guarded, (
        "現在沒有任何 dialect-guarded 的遷移了。如果是真的，這個檔案可以刪掉；"
        "如果是掃描的寫法失效了（例如改用別的方式判斷 dialect），那更要修——"
        "它現在什麼都沒在守。"
    )
