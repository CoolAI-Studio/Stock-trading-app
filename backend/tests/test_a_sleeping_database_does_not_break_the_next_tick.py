"""資料庫真的會睡著了，所以連線池不可以假裝它不會。

在 2026-09-05 之前，這一份的資料庫其實從來沒有休眠過（健康檢查每幾十秒就叫醒它一
次）。那個問題修掉之後，收盤後的兩次輪詢之間有二十幾分鐘沒有人碰它——而免費方案閒置
5 分鐘就把運算單元收起來，**收起來的時候會把連線關掉**。

於是每一次收盤後的輪詢，拿到的都是一條對面已經關掉的連線。Neon 自己的 SQLAlchemy 指
南講的就是這件事：

    the application is trying to reuse a connection after the Neon compute has
    been suspended due to inactivity  →  SSL SYSCALL error: EOF detected

它給的兩條設定，這裡兩條都要有：

    pool_pre_ping   拿出來用之前先確認還活著（本來就有）
    pool_recycle    「set to a value less than or equal to the scale to zero
                    setting configured for your compute」

少了第二條不會壞（pre-ping 會補救），但每一輪都白花一次來回；而收盤後那條路上，一輪
就是一次提醒的機會。
"""

from app.db.session import SCALE_TO_ZERO_SEC, make_engine


def test_a_pooled_connection_does_not_outlive_the_free_tiers_nap():
    """池子裡的連線不可以活得比資料庫的休眠門檻久。

    比門檻久 = 它一定已經被對面關掉了，而我們還要拿它去查一次才知道。
    """
    engine = make_engine("postgresql+psycopg2://u:p@db.example.invalid/app")

    # 下界不可以漏。SQLAlchemy 沒設定的時候 `_recycle` 是 -1（永不回收），而
    # `-1 <= 300` 也成立——只寫上界的話這條測試連預設值都放行，等於沒有寫。
    assert 0 < engine.pool._recycle <= SCALE_TO_ZERO_SEC, (
        "連線可以活得比 Neon 的休眠門檻久，那表示每一輪都會先拿到一條死掉的連線"
    )


def test_it_still_checks_the_connection_before_using_it():
    """pool_recycle 是省一次來回，不是取代 pre-ping。

    休眠門檻是對方的設定，我們只知道**目前**是 5 分鐘。真正的保險是用之前先問一句。
    """
    engine = make_engine("postgresql+psycopg2://u:p@db.example.invalid/app")

    assert engine.pool._pre_ping is True


def test_sqlite_still_gets_what_it_needs():
    """開發機和整個測試套件都跑在 SQLite 上，那條路不可以被順手改掉。"""
    engine = make_engine("sqlite:///./scratch.db")

    assert engine.dialect.name == "sqlite"
