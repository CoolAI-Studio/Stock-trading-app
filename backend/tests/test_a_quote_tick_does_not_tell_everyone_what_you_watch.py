"""每五秒一次，把所有帳號在看的代號送進每一個人的瀏覽器。

MEASURED: `market_loop` 發的是

    Event(type="quote.update", data={"symbols": sorted(fetched)})

沒有 user_id，所以 `ws/broadcast.py` 把它落進 `_BROADCAST_TO_ALL_EVENT_TYPES`，
送給每一條連線。`fetched` 是這一輪跨**全部帳號**的策略與持倉代號的聯集。

連線當下的 snapshot 有依 user_id 過濾，所以第一眼看起來是對的。之後每一輪都沒有。

原本的註解寫著「a symbol quote is the same for everyone watching it」——那句話對
「價格」為真，對「誰在看哪些代號」為假。而後者正是這個產品裡最私人的東西之一：
一個人的持股清單。

修法是把 payload 清空。前端 `useWebSocket.ts` 收到這個型別只做
`invalidateQueries(['market-quotes'])`，從來沒有讀過 payload——每個人的瀏覽器
接著用自己的憑證去要自己的報價。功能零變化。
"""

from decimal import Decimal

from app.models.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.base import Quote, Timeframe
from app.services.market_data.service import MarketDataService
from app.ws import broadcast


class _Provider:
    data_source = DataSource.YFINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(500)) for s in symbols
        }

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int):
        return []


def _service() -> MarketDataService:
    return MarketDataService(
        providers={DataSource.YFINANCE: _Provider()},
        bar_ttl_sec=dict.fromkeys(Timeframe, 60.0),
        clock=lambda: 0.0,
    )


def _account(db_session, email: str, symbol: str) -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    db_session.add(
        Strategy(
            user_id=user.id,
            name=f"strategy-{user.id}",
            symbol=symbol,
            data_source=DataSource.YFINANCE,
            source_code=(
                "class Strategy:\n"
                "    def __init__(self):\n"
                f"        self.name = 'strategy-{user.id}'\n"
                f"        self.symbol = '{symbol}'\n"
                "\n"
                "    def on_tick(self, price: float) -> str:\n"
                "        return 'HOLD'\n"
            ),
            code_hash=f"hash-{user.id}",
            is_active=True,
            default_quantity=Decimal(1),
        )
    )
    db_session.commit()
    return user


def _quote_events(events) -> list:
    return [event for event in events if event.type == "quote.update"]


def test_the_tick_event_carries_no_symbol_names_at_all(db_session):
    """兩個帳號，各自盯不同的東西。這一則事件會送給兩邊，所以它不能帶名字。"""
    _account(db_session, "owner@example.com", "2330.TW")
    _account(db_session, "someone@example.com", "AAPL")

    events = market_loop.tick_once(db=db_session, market_data_service=_service())

    published = _quote_events(events)
    assert published, "沒有發出 quote.update，前端就不會去更新報價"
    for event in published:
        assert "2330.TW" not in str(event.data)
        assert "AAPL" not in str(event.data)
        assert event.data == {}


def test_the_event_itself_still_fires(db_session):
    """前端靠它去 invalidate 自己的報價查詢。不能為了清 payload 把事件也拿掉。"""
    _account(db_session, "owner@example.com", "2330.TW")

    events = market_loop.tick_once(db=db_session, market_data_service=_service())

    assert _quote_events(events)


def test_broadcasting_to_everyone_stays_a_short_and_deliberate_list():
    """「沒帶 user_id 就廣播給所有人」是這個 bug 的預設值。清空 payload 修掉了
    這一則，但下一個被加進這份名單的事件會踩到同一個坑——所以名單本身也釘住。"""
    assert broadcast._BROADCAST_TO_ALL_EVENT_TYPES == {"quote.update"}
