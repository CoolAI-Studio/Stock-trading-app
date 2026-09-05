from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.services import db_activity

# 免費方案的資料庫閒置多久就把運算單元收起來（Neon：5 分鐘，而且關不掉）。收起來的
# 時候連線會被關掉，所以池子裡活得比這個數字久的連線一定是死的。
#
# 這件事在 2026-09-05 之前不會發生：健康檢查每幾十秒就把資料庫叫醒一次，它根本沒睡過
# （見 health._database_answer）。那條路修掉之後，收盤後兩輪之間有二十幾分鐘沒人碰它
# ——所以這裡才開始需要。
SCALE_TO_ZERO_SEC = 300


def make_engine(url: str) -> Engine:
    """照 Neon 對 SQLAlchemy 的建議設連線池。

    `pool_pre_ping` 拿出來用之前先確認還活著；`pool_recycle` 讓連線不要活得比對面的休
    眠門檻久——少了它不會壞（pre-ping 會補救），但每一輪都白花一次來回。
    """
    is_sqlite = url.startswith("sqlite")
    made = create_engine(
        url,
        pool_pre_ping=True,
        # SQLite 沒有對面，不需要（也不該）回收。
        pool_recycle=-1 if is_sqlite else SCALE_TO_ZERO_SEC - 60,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    # 記下每一句真的送出去的 SQL。免費方案是照醒著的時間計費的，所以「多久碰它一次」
    # 是這個部署活不活得過這個月的關鍵數字，而 /healthz 讀得到它、不用碰資料庫。
    db_activity.watch(made)
    return made


engine = make_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session]:
    """FastAPI dependency: one Session per request, closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
