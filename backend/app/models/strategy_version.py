"""策略每一次內容變更留下的一筆快照。

＊ 為什麼需要它。

`source_code` 改了就沒了。而這個 app 的使用者不會寫 Python——他最可能的操作是「讓
AI 改一版試試看」，然後在半夜收到一個他不想要的訊號。沒有版本歷史的話，他唯一的路
是再叫 AI 改回來，而那不是同一份程式碼。

＊ 存的是**變更後**的內容，而且建立的時候就存第一版。

只在變更時存「變更前」的話，最新的那一版永遠不在清單裡，而「還原到現在這一版」這個
念頭會讓人困惑。存變更後、加上建立時的第一版，清單就是完整的歷史，最新的那一筆永遠
等於現在在跑的東西。

＊ 只有內容變更才留一筆。

改名字、改代號不算。每一次無關的編輯都存一版的話，他要找的那一版會被埋在幾十筆長得
一模一樣的紀錄裡——而那等於沒有版本歷史。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import utcnow


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    source_code: Mapped[str] = mapped_column(Text)
    # 參數也是「這支策略是什麼」的一部分：調了一個參數之後績效變差，跟改了程式碼之
    # 後變差一樣需要退路，而參數改動不會動到 source_code。
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    code_hash: Mapped[str] = mapped_column(String(64))
    # 誰改的：manual / ai / restore。
    #
    # 「restore」自己一類是刻意的：一個看起來跟三個月前一模一樣的版本，如果沒有標
    # 明它是還原來的，他會以為自己的編輯不見了。
    author: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
