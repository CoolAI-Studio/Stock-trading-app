"""系統更新之後編不過的策略，不可以被永久停用。

＊ 這件事今天就會發生，而且會安靜地發生。

`market_loop.tick_once` 裡，編譯失敗走 `_record_strategy_error`：連續五次就
`is_active = False`，而**沒有任何東西會把它打開**。輪詢五秒一次，所以是二十五秒。

平常這是對的：一支編不過的策略不會自己好，一直重試只是浪費每一輪的時間。

但如果編不過的原因是**我們動了沙箱**（收緊 `_ALLOWED_MODULES`、改了 `compile_strategy`
的檢查、換了策略的介面），那麼在他按下更新之後的二十五秒內，他每一支用到那個名字
的策略都會被永久關掉——而畫面上只寫著「停用」。他不會知道那是我們造成的，也不會知
道要怎麼打開。

**提醒全面停擺，而且沒有任何一個東西變紅。**

＊ 這跟 #18 學到的是同一件事。

那次的來源是子行程壞掉（`WorkerUnavailable` → `_record_feed_problem`，不累積、不
停用）。這次的來源是我們自己的更新，但終點一樣：基礎設施的問題不可以走「停用使用
者的東西」那條路。

＊ 但保護不能做過頭。

一支從一開始就打錯字的新策略，還是要照舊算它的錯——不然「五次就停用」這條保護就整
個消失了，而它本來是有理由存在的。所以分界是「**它在上一個版本編得過嗎**」，不是
「編譯失敗一律不算」。
"""

from decimal import Decimal

import pytest

from app.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService

WORKS = """
class Strategy:
    def __init__(self):
        self.name = "fine"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""

# 用到一個我們有一天可能會收掉的名字。這裡直接寫成編不過的樣子。
BROKEN = """
class Strategy:
    def __init__(self):
        self.name = "broken"
        self.symbol = "AAPL"
        this is not python
"""


@pytest.fixture
def service() -> MarketDataService:
    return MarketDataService(
        providers={DataSource.YFINANCE: MockProvider(base_prices={"AAPL": 100.0})}
    )


def _user(db_session) -> User:
    user = User(email="upgrade@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _strategy(db_session, user, source: str, **overrides) -> Strategy:
    overrides.setdefault("is_active", True)
    strategy = Strategy(
        user_id=user.id,
        name="s",
        symbol="AAPL",
        source_code=source,
        code_hash="irrelevant",
        default_quantity=Decimal(1),
        **overrides,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _tick(db_session, service, times: int = 1):
    for _ in range(times):
        market_loop.tick_once(db=db_session, market_data_service=service)


def test_a_working_strategy_records_the_version_it_compiled_under(
    db_session, service, monkeypatch, published_events
):
    """編得過的時候要記下當時的版本。

    沒有這個紀錄，就沒有辦法分辨「一直都壞」和「更新之後才壞」——而那個分辨正是這
    整件事的全部。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, WORKS)

    _tick(db_session, service)

    db_session.refresh(strategy)
    assert strategy.last_compiled_version == "aaaaaaa"


def test_a_strategy_that_stopped_compiling_after_an_upgrade_is_not_retired(
    db_session, service, monkeypatch, published_events
):
    """**這一條是這張票的全部意義。**

    它在上一個版本編得過，更新之後編不過——那是我們造成的，所以不累積、不停用。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, WORKS)
    _tick(db_session, service)
    db_session.refresh(strategy)
    assert strategy.last_compiled_version == "aaaaaaa"

    # 更新了，而且新版本編不過他的程式碼。
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "bbbbbbb")
    strategy.source_code = BROKEN
    db_session.commit()

    _tick(db_session, service, times=10)  # 遠超過 _MAX_CONSECUTIVE_ERRORS

    db_session.refresh(strategy)
    assert strategy.is_active, "系統更新把使用者的策略永久關掉了"
    assert strategy.consecutive_errors == 0, "算成了策略的錯"
    assert strategy.last_error, "什麼都沒說"


def test_the_message_says_it_was_the_upgrade_not_his_code(
    db_session, service, monkeypatch, published_events
):
    """訊息要講對是誰的錯。

    「編譯失敗」這句話會讓他去改一段其實沒有問題的程式碼——而他改不動，因為問題不
    在那裡。這個 repo 對子行程壞掉也是同一個處理：講清楚是哪一種。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, WORKS)
    _tick(db_session, service)

    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "bbbbbbb")
    strategy.source_code = BROKEN
    db_session.commit()
    _tick(db_session, service)

    db_session.refresh(strategy)
    assert "更新" in strategy.last_error, strategy.last_error
    assert "aaaaaaa" in strategy.last_error, f"沒說上一個能用的版本是哪一個：{strategy.last_error}"


def test_the_owner_is_told_once_not_every_poll(db_session, service, monkeypatch, published_events):
    """要告訴他，但不可以每五秒轟一次。

    一支不會發訊號的策略等於提醒停擺，所以他必須知道。但輪詢是五秒一次，而每一輪
    都發一次通知會讓他關掉通知——那比不通知更糟。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, WORKS)
    _tick(db_session, service)

    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "bbbbbbb")
    strategy.source_code = BROKEN
    db_session.commit()
    published_events.clear()

    _tick(db_session, service, times=5)

    told = [event for event in published_events if event.type == "strategy.error"]
    assert len(told) == 1, f"發了 {len(told)} 次通知，應該只有一次"


def test_a_brand_new_strategy_with_a_typo_is_still_his_own_fault(
    db_session, service, monkeypatch, published_events
):
    """保護不能做過頭。

    一支從來沒編過的策略打錯字，還是要照舊算它的錯——不然「連續五次就停用」這條保
    護就整個消失了，而它本來是有理由存在的：一支編不過的策略不會自己好，而每一輪
    都重試它是在浪費盯盤的時間。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, BROKEN)

    _tick(db_session, service, times=market_loop._MAX_CONSECUTIVE_ERRORS)

    db_session.refresh(strategy)
    assert not strategy.is_active, "從來沒編過的壞策略應該照舊被停用"


def test_a_strategy_broken_within_the_same_version_is_still_his_own_fault(
    db_session, service, monkeypatch, published_events
):
    """他自己把程式碼改壞，版本沒變——照舊算他的錯。

    分界是「它在上一個版本編得過嗎」，不是「編譯失敗一律不算」。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, WORKS)
    _tick(db_session, service)

    # 同一個版本，是他改壞的。
    strategy.source_code = BROKEN
    db_session.commit()
    _tick(db_session, service, times=market_loop._MAX_CONSECUTIVE_ERRORS)

    db_session.refresh(strategy)
    assert not strategy.is_active, "同一個版本裡改壞的策略應該照舊被停用"


def test_it_recovers_when_the_code_compiles_again(
    db_session, service, monkeypatch, published_events
):
    """他把程式碼改好之後，一切回到正常。

    包括「下一次再壞掉會照舊累積」——不然一支曾經撞上更新的策略就永遠免疫了。
    """
    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "aaaaaaa")
    user = _user(db_session)
    strategy = _strategy(db_session, user, WORKS)
    _tick(db_session, service)

    monkeypatch.setattr(market_loop.build_info, "commit", lambda: "bbbbbbb")
    strategy.source_code = BROKEN
    db_session.commit()
    _tick(db_session, service, times=3)

    # 修好了。
    strategy.source_code = WORKS
    db_session.commit()
    _tick(db_session, service)

    db_session.refresh(strategy)
    assert strategy.last_compiled_version == "bbbbbbb"
    assert strategy.last_error is None
