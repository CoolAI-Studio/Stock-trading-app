"""留住每一版，並且留得下上限。

＊ 一筆版本 = 一次**內容**變更。

只有 `source_code` 或 `params` 動了才留。改名字、改代號不算——每一次無關的編輯都存
一版的話，他要找的那一版會被埋在幾十筆長得一模一樣的紀錄裡，而那等於沒有版本歷史。

＊ 最新的那一筆永遠等於現在在跑的東西。

建立時存第一版，之後每次內容變更再存一筆。所以清單是完整的歷史，而「這一版是不是我
現在在跑的」不需要另外判斷——它就是最上面那一筆。

＊ 上限：丟最舊的，但現在在跑的那一版永遠不丟。

免費方案的資料庫塞得爆。而如果連現在這一版都能被丟掉，那使用者最需要的那一版會在他
編輯得夠多次之後消失。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.strategy import Strategy
from app.models.strategy_version import StrategyVersion
from app.services.strategy_runtime import code_hash


def record(db: Session, strategy: Strategy, *, author: str = "manual") -> StrategyVersion | None:
    """留一筆，如果內容真的跟上一筆不一樣的話。

    比對的是內容而不是「有沒有呼叫過 PATCH」：把同一份程式碼再存一次不是一個新版
    本，而使用者按兩下儲存是很正常的事。
    """
    latest = db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    params = dict(strategy.params or {})
    unchanged = (
        latest is not None
        and latest.source_code == strategy.source_code
        and latest.params == params
    )
    if unchanged:
        return None

    version = StrategyVersion(
        strategy_id=strategy.id,
        source_code=strategy.source_code,
        params=params,
        code_hash=code_hash(strategy.source_code),
        author=author,
    )
    db.add(version)
    db.flush()
    _trim(db, strategy.id)
    return version


def _trim(db: Session, strategy_id: int) -> None:
    """超過上限就丟最舊的。

    **現在在跑的那一版（最新的那一筆）永遠不丟。** 它是唯一一個他一定需要的，而一
    個會把它丟掉的清理，會在他編輯得夠多次之後安靜地把它拿走。
    """
    keep = max(1, settings.STRATEGY_VERSION_LIMIT)
    rows = (
        db.execute(
            select(StrategyVersion.id)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.id.desc())
        )
        .scalars()
        .all()
    )
    for old_id in rows[keep:]:
        db.delete(db.get(StrategyVersion, old_id))


def listing(db: Session, strategy_id: int) -> list[StrategyVersion]:
    """最新的在前面。他要找的通常是「剛剛那一版」。"""
    return list(
        db.execute(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.id.desc())
        )
        .scalars()
        .all()
    )
