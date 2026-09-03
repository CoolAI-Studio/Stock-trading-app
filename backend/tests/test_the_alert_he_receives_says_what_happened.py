"""手機上跳出來的那一行，要說得出發生了什麼事。

＊ 這是這個產品的產出本身。

整個系統——盯盤、策略、子行程、重送佇列——存在的理由，就是為了在事情發生的時候送出
那一行字。它是使用者唯一真正會讀的東西。

而預設那條路送出去的是：

    New pending order #42 -- review it in the dashboard.

三個問題，一個比一個嚴重：

一、**它沒有說發生了什麼。** 沒有代號、沒有買賣、沒有價格、沒有哪一支策略。他站在
    月台上收到這一行，得先打開 app 才知道是哪一檔。而提醒的意義正是「不用一直看
    著」——一則要他去查的提醒，等於把那件事又還給他。

二、**它是英文的。** 整個產品是繁體中文：畫面、設定引導、備份信、看門狗的信。唯獨
    真正送到他手上的那一行不是。

三、**「pending order」聽起來像下單了。** 這個專案不做券商 API（CLAUDE.md 第一段），
    那一列是一筆**提醒紀錄**，真正下單要他自己去券商 App。一則讓他以為單子已經送出
    去的提醒，比沒有提醒危險。

＊ 對照組就在隔壁。

alert_only 那條路早就說得很完整（`test_alert_message_names_the_strategy_symbol_side_and_price`
守著）：策略名、代號、買賣、價格。也就是說**預設的那條路比 alert_only 那條差**，而
預設才是大多數人會走的。

＊ 為什麼要去資料庫撈。

`order.created` 事件裡只有 `order_id` 和 `user_id`。要說出代號和價格就得撈那一列——
而 dispatcher 手上本來就有 session。不把欄位塞進事件裡，是因為那份資料會過期：事件
在佇列裡等的時候，那一列可能已經被確認或取消了。
"""

from decimal import Decimal

import pytest

from app.enums import DataSource, OrderSide, OrderSource, OrderStatus
from app.models.order import Order
from app.models.strategy import Strategy
from app.models.user import User
from app.services.events import Event
from app.services.notification import dispatcher

TICK_SOURCE = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        self.name = '均線策略'\n"
    "        self.symbol = '2330.TW'\n"
    "    def on_tick(self, price):\n"
    "        return 'HOLD'\n"
)


@pytest.fixture
def owner(db_session) -> User:
    user = User(email="alerts@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _an_order(db_session, owner: User, *, strategy: Strategy | None = None) -> Order:
    order = Order(
        user_id=owner.id,
        strategy_id=strategy.id if strategy else None,
        source=OrderSource.STRATEGY,
        symbol="2330.TW",
        side=OrderSide.BUY,
        quantity=Decimal(1),
        signal_price=Decimal("900.5"),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def _a_strategy(db_session, owner: User) -> Strategy:
    strategy = Strategy(
        user_id=owner.id,
        name="均線策略",
        symbol="2330.TW",
        data_source=DataSource.YFINANCE,
        source_code=TICK_SOURCE,
        code_hash="irrelevant-for-tests",
        is_active=True,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _message(db_session, order: Order) -> str:
    return dispatcher._format_message(
        Event(type="order.created", data={"order_id": order.id, "user_id": order.user_id}),
        db_session,
    )


def test_it_says_which_stock(db_session, owner):
    """沒有代號的提醒，等於要他自己去查——而那正是提醒要替他省下的事。"""
    order = _an_order(db_session, owner)

    assert "2330.TW" in _message(db_session, order)


def test_it_says_buy_or_sell(db_session, owner):
    """買和賣是相反的動作。少了這一格，這則提醒沒有任何可以照著做的內容。"""
    order = _an_order(db_session, owner)

    assert "買" in _message(db_session, order)


def test_it_says_at_what_price(db_session, owner):
    """訊號價是他判斷「現在還來不來得及」的依據。"""
    order = _an_order(db_session, owner)

    assert "900.5" in _message(db_session, order)


def test_it_says_which_of_his_alerts_fired(db_session, owner):
    """他可能有五支策略盯著同一檔。哪一支響了，決定他要不要理它。

    名字，不是 id——「策略 7」對他不是一個可以拿去做事的東西。
    """
    strategy = _a_strategy(db_session, owner)
    order = _an_order(db_session, owner, strategy=strategy)

    assert "均線策略" in _message(db_session, order)


def test_it_does_not_sound_like_an_order_was_placed(db_session, owner):
    """這個專案不接券商 API，那一列是提醒紀錄，不是委託。

    讓他以為單子已經送出去的提醒，比沒有提醒危險：他會什麼都不做，然後以為部位已經
    建好了。
    """
    order = _an_order(db_session, owner)

    message = _message(db_session, order)
    assert "提醒" in message or "訊號" in message
    assert "下單" in message or "券商" in message


def test_it_is_in_the_same_language_as_the_rest_of_the_product(db_session, owner):
    """畫面、設定引導、備份信、看門狗的信都是繁體中文，只有這一行不是。

    而這一行是他唯一真的會讀的那一行。
    """
    order = _an_order(db_session, owner)

    message = _message(db_session, order)
    assert any("一" <= ch <= "鿿" for ch in message), message


def test_an_order_that_has_since_vanished_still_produces_something(db_session, owner):
    """撈不到那一列的時候不可以炸掉。

    事件在重送佇列裡等的時候，那一列可能已經被刪掉了。這裡拋出去的話，
    `_deliver_to_channel` 外面那層會把它記成「這個管道整個炸了」——一個查不到的舊訂單
    不該長成那個樣子。
    """
    order = _an_order(db_session, owner)
    order_id = order.id
    db_session.delete(order)
    db_session.commit()

    message = dispatcher._format_message(
        Event(type="order.created", data={"order_id": order_id, "user_id": owner.id}),
        db_session,
    )

    assert str(order_id) in message
