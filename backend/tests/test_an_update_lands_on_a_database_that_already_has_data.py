"""遷移要跑得過一個**已經有資料**的資料庫，不是只跑得過一個空的。

＊ 這一條補的是 CI 結構上看不到的東西。

CI 有一步叫「Migrations apply from empty, and match the models」，而那一句誠實：
**from empty**。測試套件自己更遠——它用 `create_all` 直接照模型建表，一次遷移都不
跑。

所以整個專案裡，`alembic upgrade head` 只被一種資料庫驗證過：**空的、剛出生的那
一種**。而使用者的資料庫不是那一種。他的裡面有帳號、有策略、有三個月的通知紀錄，
而更新是在那上面跑的。

兩者差在哪，是有名字的：

  * 加一個 NOT NULL 的欄位而沒有 server_default —— 空表加得上去，有資料的表加不上
  * 加一個 UNIQUE 的約束 —— 空表永遠沒有重複，有資料的表可能有
  * 改欄位型別 —— 空表沒有東西要轉換
  * 用 `op.execute` 寫的資料搬移 —— 空表跑起來像成功

每一種在 CI 上都是綠的，而在他那邊是「容器起不來」。

＊ 為什麼這件事在這個專案特別嚴重。

他的副本追著 `stable` 自動部署（#52）。所以我們每推一版，就是在別人的資料庫上跑一
次遷移，而他不在場、沒有 CI、也不知道我們改了什麼。遷移失敗的話容器起不來，而
`scripts/start.py` 那道保險（#51）雖然會讓他不至於被鎖進設定頁，但那是**止血**，
不是不要流血。

＊ 做法：每一版都當成「他停在這裡」試一次。

對每一個 revision：開一個乾淨的資料庫、升到那一版、**把每一張表都塞一列**，然後
`upgrade head`。塞得進去的表就塞——塞不進去的（外鍵湊不齊之類）跳過並記下來，因為
一個「什麼都沒塞成」的版本會安靜地退化成 CI 已經有的那個空資料庫測試。
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, insert, inspect

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _revisions() -> list[str]:
    """由舊到新，每一個 revision。"""
    script = ScriptDirectory.from_config(_config())
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def _value(column):
    """一個塞得進這一格的值。

    形狀跟 scripts/audit.py 的 `_column_value` 一樣，理由也一樣：型別對不上就整張表
    塞不進去，而一張塞不進去的表會安靜地退回「空資料庫」——那正是這個檔案要補的洞。
    """
    try:
        python_type = column.type.python_type
    except (NotImplementedError, AttributeError):
        return "x"

    if isinstance(python_type, type) and issubclass(python_type, bool):
        return False
    if isinstance(python_type, type) and issubclass(python_type, int):
        return 1
    if isinstance(python_type, type) and issubclass(python_type, float):
        return 1.0
    if isinstance(python_type, type) and issubclass(python_type, Decimal):
        return Decimal(1)
    if isinstance(python_type, type) and issubclass(python_type, datetime):
        return datetime.now(UTC)
    if isinstance(python_type, type) and issubclass(python_type, dict):
        return {}
    if isinstance(python_type, type) and issubclass(python_type, list):
        return []

    length = getattr(column.type, "length", None)
    text = "seed"
    return text[:length] if length is not None and length < len(text) else text


def _is_int(column) -> bool:
    try:
        return isinstance(column.type.python_type, type) and issubclass(
            column.type.python_type, int
        )
    except (NotImplementedError, AttributeError):
        return False


def _fill_every_table(engine) -> list[str]:
    """每一張表塞一列。回傳真的塞進去的那幾張。

    表的順序交給 SQLAlchemy 的 `sorted_tables`（它照外鍵排），不然子表會先被塞而找不
    到父列。反射的是**當下那個 revision** 的 schema，不是模型——模型是 head 的樣子，
    拿它來塞舊版的表就會對不上。
    """
    metadata = MetaData()
    metadata.reflect(bind=engine)
    filled: list[str] = []
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name == "alembic_version":
                continue
            # 只讓「自己會長號碼」的那一根主鍵留白：單一欄、整數、autoincrement。
            # 複合主鍵的每一欄都要自己給值——原本一律跳過主鍵，於是 market_bars 的
            # ts 被留白，SQLAlchemy 每一版都警告一次「主鍵不能是 NULL」，而那種噪音
            # 會讓人學會忽略警告。
            surrogate = [c for c in table.primary_key.columns]
            auto = (
                surrogate[0].name
                if len(surrogate) == 1 and surrogate[0].autoincrement and _is_int(surrogate[0])
                else None
            )
            values = {
                column.name: _value(column)
                for column in table.columns
                if column.name != auto and column.default is None and column.server_default is None
            }
            try:
                with connection.begin_nested():
                    connection.execute(insert(table).values(**values))
            except Exception:  # noqa: BLE001 -- 湊不齊就跳過，下面會斷言不能全部跳過
                continue
            filled.append(table.name)
    return filled


@pytest.mark.parametrize("revision", _revisions())
def test_an_update_from_this_version_survives_real_data(revision, tmp_path, monkeypatch):
    """停在這一版、而且裡面有資料的部署，升得到最新版。

    每一個 revision 各跑一次，因為「他停在哪一版」不是我們決定的——他可能三個月沒更
    新，也可能昨天才部署。
    """
    db_path = tmp_path / f"{revision}.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    # alembic/env.py 做的是 `from app.db.session import engine`，所以 `sqlalchemy.url`
    # 設了也沒用——它會跑到開發用的那個資料庫上去，而這裡的 tmp 檔一張表都不會有。
    # （第一版就是這樣寫的，結果 24 條全紅在「一張表都塞不進去」，而那正是為什麼那句
    # 斷言要在。）env.py 每次都重新執行，所以換掉這個屬性就夠。
    monkeypatch.setattr("app.db.session.engine", engine)
    cfg = _config()

    command.upgrade(cfg, revision)
    filled = _fill_every_table(engine)

    # 一列都沒塞成的話，這一條就退化成 CI 已經有的那個空資料庫檢查，而它會一直是綠
    # 的——那種綠燈比沒有測試更糟。
    assert filled, f"{revision}：一張表都塞不進去，這一條沒有在測它宣稱在測的事"

    command.upgrade(cfg, "head")

    # 升完之後表還在、資料還在。遷移把資料清掉也是一種「成功」，而那是使用者最不能
    # 接受的一種。
    after = inspect(engine).get_table_names()
    for name in filled:
        assert name in after, f"{revision} → head 把 {name} 這張表弄不見了"
    engine.dispose()
