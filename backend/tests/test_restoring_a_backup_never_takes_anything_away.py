"""還原是「把備份裡的東西拿回來」，不是「把現在這一份換掉」。

備份檔一直都做得出來（每天寄到他信箱），但**把它倒回去的按鈕從來沒有做**。而一個語
意沒想清楚的還原按鈕，會用一次點擊毀掉他所有的東西——所以先把規則釘在這裡。

＊ 第一條：只加，不蓋，不刪。

他按錯了也只是多了一些東西，而多出來的東西刪得掉，被蓋掉的東西回不來。方向不對稱，
所以往「多出來」錯。

＊ 第二條：加進來的東西不可以自己動起來。

「一律新增」單獨拿出來會製造一個更糟的問題：

    兩份一樣的策略同時在跑 → 同一件事通知兩次
    兩份一樣的持股         → 系統以為他部位加倍 → 停損和風控全部照錯的數字算

第一個是這個產品最不能發生的事（CLAUDE.md 第一段），第二個會讓他照著錯的數字做決
定。所以策略和通知管道**一律以停用的狀態進來**，他自己打開他要的那幾個；而持股和自
選股有 UNIQUE(user, symbol)，就「沒有的才加」。

＊ 第三條：帳號歸這一份部署。

一份部署只有一個帳號，而備份檔裡的信箱只是資訊。資料一律掛到**現在登入的這個人**底
下，也絕不動他的密碼（備份裡本來就沒有密碼）。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.enums import (
    ChannelType,
    DataSource,
    NotificationStatus,
    OrderSide,
    OrderSource,
    OrderStatus,
)
from app.models.notification import NotificationChannel
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User
from app.models.watchlist import WatchlistItem
from app.services import backup

SOURCE = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        self.name = 's'\n"
    "    def on_tick(self, price):\n"
    "        return 'HOLD'\n"
)


@pytest.fixture
def owner(db_session) -> User:
    user = User(email="owner@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def other(db_session) -> User:
    """備份是「另一個人」做的——他重新部署過、信箱打不一樣，或那是別份部署的檔案。"""
    user = User(email="somebody-else@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _fill(db, user: User) -> Strategy:
    """一份有東西的帳號：策略、通知管道、持股、自選股、風控、訊號和提醒紀錄。"""
    strategy = Strategy(
        user_id=user.id,
        name="均線策略",
        symbol="2330.TW",
        data_source=DataSource.YFINANCE,
        source_code=SOURCE,
        code_hash="whatever",
        is_active=True,
        stop_loss_pct=Decimal("5.5"),
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)

    db.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="我的手機",
            config_encrypted={"bot_token": "secret-token", "chat_id": "42"},
            is_enabled=True,
        )
    )
    db.add(
        Position(
            user_id=user.id,
            symbol="2330.TW",
            quantity=Decimal(1000),
            avg_entry_price=Decimal("880.5"),
            strategy_id=strategy.id,
        )
    )
    db.add(WatchlistItem(user_id=user.id, symbol="2454.TW", data_source=DataSource.YFINANCE))
    db.add(RiskSettings(user_id=user.id, capital=Decimal(500000), stop_loss_pct=Decimal("3.5")))
    db.add(
        Order(
            user_id=user.id,
            strategy_id=strategy.id,
            source=OrderSource.STRATEGY,
            symbol="2330.TW",
            side=OrderSide.BUY,
            quantity=Decimal(1),
            signal_price=Decimal("900.5"),
            status=OrderStatus.PENDING,
        )
    )
    db.add(
        StrategyAlert(
            user_id=user.id,
            strategy_id=strategy.id,
            symbol="2330.TW",
            side=OrderSide.BUY,
            price=Decimal(901),
            status=NotificationStatus.SENT,
        )
    )
    db.commit()
    return strategy


def _snapshot_of(db, user: User) -> dict:
    return backup.read(backup.create(db, user, "correct-horse"), "correct-horse")


def _aware(value: datetime) -> datetime:
    """SQLite 存回來的時間是 naive 的：`DateTime(timezone=True)` 在它上面沒有時區。

    測試跑在 SQLite 上、線上是 Postgres，所以這是環境差異，不是還原的問題——app 自己
    也到處在做同一件事（`schemas/common.py`、`market_loop`、`backup_schedule`）。
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# --- 只加，不蓋，不刪 --------------------------------------------------------


def test_what_he_already_has_is_still_there_afterwards(db_session, owner, other):
    """這是整個設計的第一條。還原不可以拿走任何東西。"""
    _fill(db_session, other)
    mine = _fill(db_session, owner)
    mine_id = mine.id

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    still = db_session.get(Strategy, mine_id)
    assert still is not None
    assert still.is_active is True, "他自己那一支被還原關掉了"
    assert still.name == "均線策略", "他自己那一支被改名了"


def test_his_own_risk_settings_are_not_overwritten(db_session, owner, other):
    """停損設定被一個舊檔案換掉，下一次穿價就是照錯的數字算。"""
    _fill(db_session, other)
    _fill(db_session, owner)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    mine = db_session.query(RiskSettings).filter(RiskSettings.user_id == owner.id).one()
    assert mine.stop_loss_pct == Decimal("3.5")


def test_a_position_he_already_holds_is_not_duplicated(db_session, owner, other):
    """重複的話系統會以為他部位加倍，停損和風控全部算錯。"""
    _fill(db_session, other)
    _fill(db_session, owner)

    report = backup.restore(db_session, owner, _snapshot_of(db_session, other))

    held = db_session.query(Position).filter(Position.user_id == owner.id).all()
    assert len(held) == 1
    assert report.positions_skipped == 1


def test_a_watchlist_row_he_already_has_is_not_duplicated(db_session, owner, other):
    _fill(db_session, other)
    _fill(db_session, owner)

    report = backup.restore(db_session, owner, _snapshot_of(db_session, other))

    rows = db_session.query(WatchlistItem).filter(WatchlistItem.user_id == owner.id).all()
    assert len(rows) == 1
    assert report.watchlist_skipped == 1


# --- 加進來的東西不可以自己動起來 --------------------------------------------


def test_restored_strategies_arrive_switched_off(db_session, owner, other):
    """兩份一樣的策略同時在跑 ＝ 同一件事通知兩次。

    這是這個產品最不能發生的事，而它是「都保留」這個決定唯一的代價——所以在這裡擋掉。
    """
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    restored = db_session.query(Strategy).filter(Strategy.user_id == owner.id).all()
    assert len(restored) == 1
    assert restored[0].is_active is False


def test_restored_channels_arrive_switched_off(db_session, owner, other):
    """通知管道是真的會把東西送出去的那一個，所以更不能自己醒著。"""
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    channels = (
        db_session.query(NotificationChannel).filter(NotificationChannel.user_id == owner.id).all()
    )
    assert len(channels) == 1
    assert channels[0].is_enabled is False


def test_a_pending_signal_from_the_past_does_not_come_back_alive(db_session, owner, other):
    """一張三個月前的待確認訊號被復活成「現在等你確認」，是拿一個早就過去的價格問他
    要不要動作——而他不會知道那是舊的。歷史留著，行為不留。
    """
    _fill(db_session, other)

    report = backup.restore(db_session, owner, _snapshot_of(db_session, other))

    orders = db_session.query(Order).filter(Order.user_id == owner.id).all()
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.EXPIRED
    assert report.expired_pending == 1


def test_a_signal_that_was_already_history_keeps_its_status(db_session, owner, other):
    """只有待確認的要改。把已成交改成已過期就是竄改他的紀錄。"""
    strategy = _fill(db_session, other)
    db_session.add(
        Order(
            user_id=other.id,
            strategy_id=strategy.id,
            source=OrderSource.STRATEGY,
            symbol="2330.TW",
            side=OrderSide.SELL,
            quantity=Decimal(1),
            signal_price=Decimal(910),
            status=OrderStatus.CONFIRMED,
        )
    )
    db_session.commit()

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    statuses = {order.status for order in db_session.query(Order).filter(Order.user_id == owner.id)}
    assert OrderStatus.CONFIRMED in statuses


# --- 這一份是誰的 ------------------------------------------------------------


def test_everything_lands_under_the_account_that_is_logged_in(db_session, owner, other):
    """一份部署只有一個帳號，備份裡的信箱只是資訊。"""
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    restored = db_session.query(Strategy).filter(Strategy.user_id == owner.id).all()
    assert len(restored) == 1
    assert restored[0].user_id == owner.id


def test_it_never_creates_or_renames_an_account(db_session, owner, other):
    """備份裡有一個信箱。它不可以變成一個帳號，也不可以改掉現在這一個。"""
    before = {user.id: user.email for user in db_session.query(User)}

    _fill(db_session, other)
    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    after = {user.id: user.email for user in db_session.query(User)}
    assert after == before


# --- 名字：撞得到，而且要看得出來哪個是還原進來的 ----------------------------


def test_a_restored_strategy_is_recognisable_on_screen(db_session, owner, other):
    """畫面上兩個一模一樣的名字，他分不出哪一個是還原進來的。"""
    _fill(db_session, other)
    _fill(db_session, owner)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    names = [s.name for s in db_session.query(Strategy).filter(Strategy.user_id == owner.id)]
    assert "均線策略" in names
    assert any(name.startswith("均線策略") and name != "均線策略" for name in names), names


def test_the_same_backup_can_be_restored_twice(db_session, owner, other):
    """`UNIQUE(user_id, name)` 會擋，而他會倒第二次——多半正是因為第一次沒看清楚。"""
    _fill(db_session, other)
    snapshot = _snapshot_of(db_session, other)

    backup.restore(db_session, owner, snapshot)
    backup.restore(db_session, owner, snapshot)

    assert db_session.query(Strategy).filter(Strategy.user_id == owner.id).count() == 2


# --- 型別：價格不可以在來回一趟之後變成字串 ----------------------------------


def test_a_price_survives_the_round_trip_as_a_number(db_session, owner, other):
    """JSON 沒有 Decimal，所以出去的時候是字串。回來沒有轉回去的話，比大小會用字典
    序——「9」會比「10」大，而那是一個不會拋錯、只會算錯的失敗。
    """
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    restored = db_session.query(Strategy).filter(Strategy.user_id == owner.id).one()
    assert restored.stop_loss_pct == Decimal("5.5")


def test_an_enum_survives_the_round_trip(db_session, owner, other):
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    restored = db_session.query(Strategy).filter(Strategy.user_id == owner.id).one()
    assert restored.data_source == DataSource.YFINANCE


def test_a_timestamp_survives_the_round_trip(db_session, owner, other):
    """建立時間是他判斷「這是哪一段的紀錄」的依據。"""
    _fill(db_session, other)
    made = _snapshot_of(db_session, other)

    backup.restore(db_session, owner, made)

    restored = db_session.query(Strategy).filter(Strategy.user_id == owner.id).one()
    assert isinstance(restored.created_at, datetime)
    assert abs((_aware(restored.created_at) - datetime.now(UTC)).total_seconds()) < 3600


def test_the_channel_secret_comes_back_usable(db_session, owner, other):
    """token 在檔案裡是明文（包在備份自己的密碼信封裡），所以還原之後那個管道是能用
    的——他只要打開它。倒回來是一團解不開的東西的話，還原等於沒有做。
    """
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    channel = (
        db_session.query(NotificationChannel).filter(NotificationChannel.user_id == owner.id).one()
    )
    assert channel.config_encrypted["bot_token"] == "secret-token"


# --- 版本 --------------------------------------------------------------------


def test_a_backup_from_a_newer_version_is_refused(db_session, owner):
    """往回搬沒有安全的做法：我們不知道未來的欄位是什麼意思，而猜錯的代價是他以為還
    原好了。拒絕，並且說怎麼辦。
    """
    with pytest.raises(backup.BackupError) as caught:
        backup.restore(db_session, owner, {"format_version": backup.FORMAT_VERSION + 1})

    assert "更新" in str(caught.value)


def test_an_older_backup_is_accepted(db_session, owner, other):
    """舊的要倒得進來，不然「備份」這個功能對三個月前的檔案是沒有意義的。"""
    _fill(db_session, other)
    snapshot = _snapshot_of(db_session, other)
    snapshot["format_version"] = 1
    snapshot.pop("watchlist", None)  # 假裝那一版還沒有這張表

    report = backup.restore(db_session, owner, snapshot)

    assert report.strategies == 1
    assert report.watchlist == 0


def test_an_empty_backup_does_not_explode(db_session, owner):
    """全新的部署備份出來就是這個形狀。"""
    report = backup.restore(db_session, owner, {"format_version": backup.FORMAT_VERSION})

    assert report.strategies == 0


# --- 報告：他需要知道「有東西是停用的」 --------------------------------------


def test_the_report_says_enough_to_tell_him_what_to_do_next(db_session, owner, other):
    """「還原完成」四個字說不出他真正需要知道的那件事：策略是停用的，等他打開。

    沒有這些數字的話，他會以為提醒已經在跑了——而那正是這個產品唯一不能失效的東西。
    """
    _fill(db_session, other)

    report = backup.restore(db_session, owner, _snapshot_of(db_session, other))

    assert report.strategies == 1
    assert report.channels == 1
    assert report.orders == 1
    assert report.alerts == 1
    assert report.positions == 1


def test_an_alert_record_follows_its_strategy_to_the_new_id(db_session, owner, other):
    """提醒紀錄指的是備份裡那支策略的舊 id。不重新對應的話它會指到別人的策略上，
    或者指到一個不存在的東西。
    """
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    strategy = db_session.query(Strategy).filter(Strategy.user_id == owner.id).one()
    alerts = db_session.query(StrategyAlert).filter(StrategyAlert.user_id == owner.id).all()
    assert len(alerts) == 1
    assert alerts[0].strategy_id == strategy.id


def test_an_order_follows_its_strategy_too(db_session, owner, other):
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    strategy = db_session.query(Strategy).filter(Strategy.user_id == owner.id).one()
    order = db_session.query(Order).filter(Order.user_id == owner.id).one()
    assert order.strategy_id == strategy.id


def test_a_position_follows_its_strategy_too(db_session, owner, other):
    """持股上的 strategy_id 決定停損由誰負責。指錯的話停損不會被檢查。"""
    _fill(db_session, other)

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    strategy = db_session.query(Strategy).filter(Strategy.user_id == owner.id).one()
    position = db_session.query(Position).filter(Position.user_id == owner.id).one()
    assert position.strategy_id == strategy.id


def test_an_old_created_at_is_kept_rather_than_stamped_now(db_session, owner, other):
    """他的歷史紀錄要保持是歷史。全部蓋成「現在」的話，那份紀錄就沒有意義了。"""
    strategy = _fill(db_session, other)
    long_ago = datetime.now(UTC) - timedelta(days=90)
    db_session.query(Order).filter(Order.strategy_id == strategy.id).update(
        {"created_at": long_ago}
    )
    db_session.commit()

    backup.restore(db_session, owner, _snapshot_of(db_session, other))

    order = db_session.query(Order).filter(Order.user_id == owner.id).one()
    assert (datetime.now(UTC) - _aware(order.created_at)).days >= 89
