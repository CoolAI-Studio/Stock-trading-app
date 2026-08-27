"""投資組合回測：多支代號共用一份資金與風險設定。

#34 的第三項。前兩項是「同一支策略跑很多次」，這一項不一樣——**很多支代號共用一
個帳戶**，而共用帳戶會逼出三個單支回測從來不必回答的問題。三個都是「答錯了也看不
出來」的那種。

＊ 一、同一天兩支都要買，而錢只夠一支。

單支回測沒有這個問題：錢不夠就是不夠，記一筆「買不起」。共用帳戶就有先後順序，而
**任何順序都是武斷的**——所以重點不是選對，是選一個並且說出來，且每次都一樣。不說
的話，使用者換一次代號的排列就得到不同的績效，而他不會知道為什麼。

＊ 二、各支的 K 棒日期對不齊。

台股和美股的假日不同，個股也會停牌。單支回測的時間軸就是那支的 K 棒；投組必須有一
條共同的時間軸，而那天沒有 K 棒的代號**不能被當成「價格沒變」**——它只是沒有報價。

＊ 三、權益曲線要在哪個價格上加總。

那天沒有 K 棒的持股，只能用它最後一次的收盤價入帳。那是唯一能做的事，但它讓權益曲
線裡混著**過期的價格**——而那條曲線看起來跟真的一模一樣。所以要說出來。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import portfolio_backtest
from app.services.backtest import BacktestAssumptions
from app.services.market_data.base import Bar, Timeframe

_START = datetime(2026, 1, 5, tzinfo=UTC)

ALWAYS_BUY = """
class Strategy:
    def __init__(self):
        self.name = "always_buy"
        self.symbol = "AAA"
        self.timeframe = "1d"
        self.warmup_bars = 0

    def on_bar(self, bar) -> str:
        return "BUY"
"""

BUYS_ONCE = """
class Strategy:
    def __init__(self):
        self.name = "buys_once"
        self.symbol = "AAA"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        return "BUY" if self.seen == 1 else "HOLD"
"""


def _bars(symbol: str, days: list[int], price: float = 100.0) -> list[Bar]:
    """指定的那幾天各一根。`days` 不連續，就是用來測日期對不齊的。"""
    return [
        Bar(
            symbol=symbol,
            timeframe=Timeframe.DAY_1,
            timestamp=_START + timedelta(days=day),
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=1000.0,
        )
        for day in days
    ]


@pytest.fixture(autouse=True)
def _budget(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STRATEGY_BACKTEST_TIMEOUT_SEC", 10.0)


def _cheap() -> BacktestAssumptions:
    """沒有手續費、沒有滑價。這一組要測的是資金怎麼分，不是成本怎麼算。"""
    return BacktestAssumptions(
        commission_rate=0,
        minimum_fee=0,
        slippage_rate=0,
        sell_tax_rate=0,
    )


def test_the_wallet_is_shared_not_one_per_symbol():
    """**這一條是這個功能的全部意義。**

    三支各給一份資金，那只是三次單支回測擺在一起——而使用者問的是「我這些錢一起
    下去會怎樣」。共用一個錢包，第三支就可能買不起，而那才是真的。
    """
    days = list(range(5))
    result = portfolio_backtest.run(
        source_code=ALWAYS_BUY,
        bars_by_symbol={
            "AAA": _bars("AAA", days, price=100.0),
            "BBB": _bars("BBB", days, price=100.0),
            "CCC": _bars("CCC", days, price=100.0),
        },
        assumptions=_cheap(),
    )

    # 起始資金買不起三支的話，就必須有人**因為錢不夠**而沒買到——而且那件事要有
    # 一個數字說得出來。沒有它的話，一個因為排在後面而幾乎沒買到的代號，看起來會
    # 像一支「訊號很少」的爛策略。
    opened = {leg.symbol for leg in result.legs if leg.opened > 0}
    assert opened, "一支都沒建倉，那不是共用錢包，是壞掉"
    assert result.summary.final_equity > 0

    starved = [leg for leg in result.legs if leg.skipped_for_cash > 0]
    if len(opened) < 3:
        assert starved, "有人沒買到，卻沒有任何一支說是因為錢不夠"


def test_who_gets_the_money_is_stated_and_stable():
    """順序是武斷的，所以要**說出來**，而且每次都一樣。

    不說的話，使用者換一次代號的排列就得到不同的績效，而他不會知道為什麼——他會
    以為那是策略的差別。
    """
    days = list(range(5))
    bars = {
        "AAA": _bars("AAA", days, price=100.0),
        "BBB": _bars("BBB", days, price=100.0),
        "CCC": _bars("CCC", days, price=100.0),
    }

    first = portfolio_backtest.run(
        source_code=ALWAYS_BUY, bars_by_symbol=bars, assumptions=_cheap()
    )
    again = portfolio_backtest.run(
        source_code=ALWAYS_BUY, bars_by_symbol=bars, assumptions=_cheap()
    )

    assert [leg.symbol for leg in first.legs] == [leg.symbol for leg in again.legs]
    assert first.summary.final_equity == again.summary.final_equity
    joined = " ".join(first.notes)
    assert "順序" in joined or "先" in joined, f"沒說錢不夠的時候誰先拿：{first.notes}"


def test_a_day_one_symbol_does_not_trade_on_is_not_treated_as_a_flat_price():
    """沒有 K 棒 ≠ 價格沒變。

    台股和美股的假日不同，個股也會停牌。那天沒有報價的代號不可以被當成「今天沒
    動」——它只是沒有資料，而那兩件事在權益曲線上長得一樣。
    """
    result = portfolio_backtest.run(
        source_code=BUYS_ONCE,
        bars_by_symbol={
            "AAA": _bars("AAA", [0, 1, 2, 3], price=100.0),
            # BBB 少了第 1 天和第 2 天。
            "BBB": _bars("BBB", [0, 3], price=100.0),
        },
        assumptions=_cheap(),
    )

    # 共同時間軸是聯集，不是交集——取交集會把 AAA 的兩天也丟掉。
    assert len(result.equity_curve) == 4
    stale = [point for point in result.equity_curve if point.stale_symbols]
    assert stale, "有代號那幾天沒有報價，權益曲線卻沒有標示"
    assert "BBB" in stale[0].stale_symbols


def test_the_curve_says_when_it_is_marking_at_a_stale_price():
    """權益曲線裡混著過期價格的時候，要說出來。

    那天沒有 K 棒的持股只能用最後一次的收盤價入帳——那是唯一能做的事，但它讓那條
    曲線看起來跟真的一模一樣。這個 repo 對「存下來的 K 棒」也是同一個處理：能畫，
    但要標明它不是最新的。
    """
    result = portfolio_backtest.run(
        source_code=BUYS_ONCE,
        bars_by_symbol={
            "AAA": _bars("AAA", [0, 1, 2], price=100.0),
            "BBB": _bars("BBB", [0], price=100.0),
        },
        assumptions=_cheap(),
    )

    joined = " ".join(result.notes)
    assert "最後一次" in joined or "過期" in joined or "沒有報價" in joined


def test_a_symbol_with_no_history_is_reported_not_dropped():
    """抓不到歷史的代號要留在報告上。

    安靜地少一支，會讓使用者以為他問的那個投組跑過了——而其實跑的是另一個。這個
    repo 已經被「空清單被讀成正常結果」咬過（抓不到 K 棒被當成還在暖身）。
    """
    result = portfolio_backtest.run(
        source_code=BUYS_ONCE,
        bars_by_symbol={
            "AAA": _bars("AAA", [0, 1, 2], price=100.0),
            "GONE": [],
        },
        assumptions=_cheap(),
    )

    gone = [leg for leg in result.legs if leg.symbol == "GONE"]
    assert gone, "抓不到歷史的代號被安靜地丟掉了"
    assert gone[0].note, "丟掉的原因沒有說"


def test_an_empty_portfolio_is_refused():
    with pytest.raises(portfolio_backtest.PortfolioError):
        portfolio_backtest.run(source_code=BUYS_ONCE, bars_by_symbol={}, assumptions=_cheap())


def test_each_leg_reports_its_own_result_as_well_as_the_total():
    """總分之外，每一支自己的成績也要看得到。

    只給總分的話，一支拖累整體的代號會被另一支蓋過去——而使用者要做的決定正是
    「哪一支該拿掉」。
    """
    result = portfolio_backtest.run(
        source_code=BUYS_ONCE,
        bars_by_symbol={
            "AAA": _bars("AAA", list(range(5)), price=100.0),
            "BBB": _bars("BBB", list(range(5)), price=50.0),
        },
        assumptions=_cheap(),
    )

    assert {leg.symbol for leg in result.legs} == {"AAA", "BBB"}
    for leg in result.legs:
        assert leg.summary is not None, f"{leg.symbol} 沒有自己的成績"

    # 投組的成交數**少於等於**各支單獨跑的總和，因為共用錢包的時候會有人買不到。
    #
    # 我第一版把這裡寫成相等，那是錯的：leg.summary 是「這一支單獨用全額資金跑」
    # 的成績，而兩者的差額正是這個功能要回答的東西——寫成相等等於斷言共用錢包沒有
    # 任何效果。
    standalone = sum(leg.summary.trade_count for leg in result.legs if leg.summary)
    assert result.summary.trade_count <= standalone


# --- 端點 --------------------------------------------------------------------


class _StubBarProvider:
    """AAA 每天都有，BBB 少了中間兩天。日期對不齊要在端點這一層也成立。"""

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int = 300) -> list[Bar]:
        if symbol == "AAA":
            return _bars("AAA", list(range(10)), price=100.0)
        if symbol == "BBB":
            return _bars("BBB", [0, 1, 8, 9], price=50.0)
        return []

    def get_quotes(self, symbols, **kwargs):
        return {}


@pytest.fixture
def stub_market_data():
    from app.enums import DataSource
    from app.main import app
    from app.services.market_data.service import MarketDataService, get_market_data_service

    service = MarketDataService(providers={DataSource.YFINANCE: _StubBarProvider()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


def _request(**overrides) -> dict:
    payload = {
        "source_code": BUYS_ONCE,
        "symbols": ["AAA", "BBB"],
        "start": _START.isoformat(),
        "end": (_START + timedelta(days=9)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_the_endpoint_needs_a_login(client):
    """稽查的硬性關卡。"""
    resp = client.post("/api/backtests/portfolio", json=_request())

    assert resp.status_code in (401, 403), resp.status_code


def test_the_endpoint_returns_a_leg_per_symbol_and_a_shared_curve(auth_client, stub_market_data):
    body = auth_client.post("/api/backtests/portfolio", json=_request()).json()

    assert [leg["symbol"] for leg in body["legs"]] == ["AAA", "BBB"]
    assert body["equity_curve"], "沒有權益曲線"
    # 時間軸取聯集：AAA 有十天，所以曲線就是十個點，不是 BBB 的四個。
    assert len(body["equity_curve"]) == 10
    assert body["notes"]


def test_a_symbol_that_cannot_be_fetched_still_shows_up(auth_client, stub_market_data):
    """抓不到的代號要留在報告上，而且不能拖垮整個投組。"""
    body = auth_client.post(
        "/api/backtests/portfolio", json=_request(symbols=["AAA", "NOPE"])
    ).json()

    missing = [leg for leg in body["legs"] if leg["symbol"] == "NOPE"]
    assert missing and missing[0]["note"], "抓不到的代號被安靜地丟掉了"
    assert body["equity_curve"], "一支抓不到就把整個投組弄壞了"
