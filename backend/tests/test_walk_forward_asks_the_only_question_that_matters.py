"""滾動前進：用前一段挑出來的參數，在它沒看過的下一段上還成立嗎。

#34 的第二項，也是第一項（參數掃描）那句警語的解藥。

＊ 兩者問的是不同的問題。

    掃描      這組參數在**這段歷史**上表現如何
    滾動前進  這組參數在**它沒看過的資料**上表現如何

第一個問題的答案永遠找得到——網格夠大，總有一格好看。第二個問題才是使用者真正想
知道的，而它會拒絕大部分的答案。

＊ 這個功能唯一的失效模式：讓參數看到未來。

只要驗證區間的任何一根 K 棒參與過挑選，整件事就退化成一次比較貴的掃描——而**結果
會變好看**，所以沒有人會發現。這一組測試幾乎全部在測這一件事。

具體有三個漏法，三個都不會報錯：

  一、切分的時候多給一根（`bars[:split]` 寫成 `bars[:split + 1]`）。
  二、暖身從驗證區間裡拿。暖身不產生訊號，很容易被當成「不算數」——但暖身餵進去
      的價格會決定第一根被測 K 棒的指標值，而那些價格如果來自驗證區間，那就是未
      來的資料在決定現在的訊號。
  三、每一段各自抓一次 K 棒。兩次抓取之間上游做了一次除權調整，訓練段和驗證段就
      跑在兩組不同的價格上——而兩邊各自看起來都很正常。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import walk_forward
from app.services.market_data.base import Bar, Timeframe

_START = datetime(2026, 1, 5, tzinfo=UTC)

# window 小的在漲勢裡訊號多，window 大的訊號少。挑出來的那一組要真的隨區間變動，
# 不然「有沒有用未來資料」根本測不出來。
TUNABLE = """
class Strategy:
    def __init__(self):
        self.name = "tunable"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.params = {"window": 3}
        self.closes = []

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        window = self.params["window"]
        if len(self.closes) < window:
            return "HOLD"
        return "BUY" if bar.close > sum(self.closes[-window:]) / window else "SELL"
"""

# **刻意不宣告 warmup_bars**，讓呼叫端傳進來的那個數字說了算。
#
# 宣告 0 的策略不需要往回借任何一根，那條路測不到「暖身有沒有從驗證區間裡吃」——
# 而那正是這個功能最不容易看出來的漏法。
RECORDS_WHAT_IT_SAW = """
class Strategy:
    def __init__(self):
        self.name = "recorder"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        self.params = {"window": 1}
        self.seen = []

    def on_bar(self, bar) -> str:
        self.seen.append(bar.close)
        return "HOLD"
"""


def _bars(count: int = 120) -> list[Bar]:
    return [
        Bar(
            symbol="2330.TW",
            timeframe=Timeframe.DAY_1,
            timestamp=_START + timedelta(days=day),
            open=100.0 + day,
            high=101.0 + day,
            low=99.0 + day,
            close=100.5 + day,
            volume=1000.0,
        )
        for day in range(count)
    ]


@pytest.fixture(autouse=True)
def _short_budget(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STRATEGY_BACKTEST_TIMEOUT_SEC", 10.0)


def test_the_folds_never_overlap():
    """**這一條是這個功能的全部意義。**

    每一段的驗證區間，都不可以有任何一根 K 棒出現在同一段的訓練區間裡。差一根就
    夠了：那一根參與過挑選，而它的結果被算進成績——分數會變好看，而沒有東西會說。
    """
    plan = walk_forward.split(total=120, train=60, test=20, step=20)

    assert plan, "切不出任何一段"
    for fold in plan:
        assert set(range(*fold.train)) & set(range(*fold.test)) == set(), (
            f"第 {fold.index} 段的訓練和驗證重疊了：{fold.train} / {fold.test}"
        )
        assert fold.train[1] <= fold.test[0], "驗證區間必須整段在訓練區間之後"


def test_each_fold_tests_on_data_that_comes_strictly_later():
    """時間方向不可以反。

    用後面的資料挑參數、拿前面的資料驗證，在數學上完全跑得動，而且分數看起來很正
    常——但它回答的是一個沒有人會問的問題。
    """
    plan = walk_forward.split(total=200, train=80, test=20, step=20)

    for fold in plan:
        assert fold.test[0] >= fold.train[1]
    # 而且每一段的驗證區間要往前推，不是原地踏步。
    starts = [fold.test[0] for fold in plan]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_warmup_comes_from_the_training_side_not_the_tested_side():
    """暖身也是資料，也會洩漏。

    暖身不產生訊號，所以很容易被當成「不算數」。但暖身餵進去的價格會決定第一根被
    測 K 棒的指標值——那些價格如果來自驗證區間，就是未來的資料在決定現在的訊號。

    這是三個漏法裡最不容易被看出來的一個，因為它不會讓任何區間邊界看起來不對。
    """
    bars = _bars()

    report = walk_forward.run(
        source_code=RECORDS_WHAT_IT_SAW,
        bars=bars,
        grid={"window": [1, 2]},
        train=60,
        test=20,
        step=20,
        stored_warmup_bars=5,
    )

    for fold in report.folds:
        # 被測的第一根 K 棒之前的每一根，都必須來自訓練那一側。
        assert fold.warmup_from < fold.test_range[0], (
            f"第 {fold.index} 段的暖身取到了驗證區間裡的 K 棒"
        )
        # 而這才是看得到的後果，上面那一行只是算術。
        #
        # 暖身如果是從驗證區間裡吃的，被測的 K 棒就會少掉那幾根——20 根變 15 根，
        # 而每一個比率型的指標（勝率、曝險）都會跟著變，看起來卻完全正常。
        assert fold.test_summary is not None
        assert fold.test_summary.bars_tested == 20, (
            f"第 {fold.index} 段只測了 {fold.test_summary.bars_tested} 根，"
            "暖身大概是從驗證區間裡吃掉的"
        )


def test_every_fold_uses_the_same_batch_of_candles():
    """K 棒抓一次，所有段共用。

    每段各抓一次的話，兩次抓取之間上游做了一次除權調整，訓練段和驗證段就跑在兩組
    不同的價格上——而兩邊各自看起來都很正常。跟參數掃描同一個道理，只是這裡錯了會
    讓整個「有沒有過度配適」的結論失效。
    """
    bars = _bars()
    fetched: list[int] = []

    walk_forward.run(
        source_code=TUNABLE,
        bars=bars,
        grid={"window": [2, 5]},
        train=60,
        test=20,
        step=20,
        on_bars_used=fetched.append,
    )

    assert fetched == [len(bars)], f"K 棒被抓了 {len(fetched)} 次，應該只有一次"


def test_the_report_says_which_parameters_each_fold_picked():
    """每一段挑了什麼，要說出來。

    這是使用者判斷「這支策略到底穩不穩」的主要線索：如果每一段挑出來的參數都不一
    樣，那代表這個參數根本沒有一個穩定的最佳值——**那個發現比任何一個分數都有用**，
    而只給總分會把它藏起來。
    """
    report = walk_forward.run(
        source_code=TUNABLE,
        bars=_bars(),
        grid={"window": [2, 5, 10]},
        train=60,
        test=20,
        step=20,
    )

    assert report.folds
    for fold in report.folds:
        assert fold.chosen_params, f"第 {fold.index} 段沒說它挑了什麼"
        assert fold.chosen_params["window"] in (2, 5, 10)


def test_the_report_compares_in_sample_with_out_of_sample():
    """訓練段的成績和驗證段的成績要擺在一起。

    兩個數字分開看都沒有意義。擺在一起，它們回答的才是那個真正的問題：**在訓練段
    上好看多少，在沒看過的資料上還剩多少。** 落差就是過度配適的量。
    """
    report = walk_forward.run(
        source_code=TUNABLE,
        bars=_bars(),
        grid={"window": [2, 5]},
        train=60,
        test=20,
        step=20,
    )

    for fold in report.folds:
        assert fold.train_summary is not None
        assert fold.test_summary is not None


def test_not_enough_history_is_refused_rather_than_silently_giving_one_fold():
    """資料不夠就說不夠。

    一段都切不出來的時候回一個空報告，看起來像「跑完了，沒什麼發現」——而其實是
    根本沒跑。這個 repo 已經被同一種安靜的空結果咬過（抓不到 K 棒被讀成「還在暖
    身」）。
    """
    with pytest.raises(walk_forward.WalkForwardError):
        walk_forward.run(
            source_code=TUNABLE,
            bars=_bars(30),
            grid={"window": [2, 5]},
            train=60,
            test=20,
            step=20,
        )


# --- 端點 --------------------------------------------------------------------


class _StubBarProvider:
    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int = 300) -> list[Bar]:
        return _bars(200) if symbol == "2330.TW" else []

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
        "source_code": TUNABLE,
        "symbol": "2330.TW",
        "start": _START.isoformat(),
        "end": (_START + timedelta(days=199)).isoformat(),
        "grid": {"window": [2, 5]},
        "train_bars": 60,
        "test_bars": 20,
        "step_bars": 20,
    }
    payload.update(overrides)
    return payload


def test_the_endpoint_needs_a_login(client):
    """稽查的硬性關卡。滾動前進比掃描還貴——它是 N 段 × 一整個網格。"""
    resp = client.post("/api/backtests/walk-forward", json=_request())

    assert resp.status_code in (401, 403), resp.status_code


def test_the_endpoint_puts_in_sample_next_to_out_of_sample(auth_client, stub_market_data):
    """兩個成績要並排回來。

    這是使用者唯一能自己判斷「這支策略是不是被調出來的」的方式：訓練段好看是應該
    的，那組參數就是在那裡挑出來的；真正的問題是它在沒看過的資料上還剩多少。
    """
    body = auth_client.post("/api/backtests/walk-forward", json=_request()).json()

    assert body["folds"], body
    for fold in body["folds"]:
        assert fold["test_from"] >= fold["train_to"], "驗證區間跑到訓練區間前面去了"
        assert fold["chosen_params"]
        assert fold["train_summary"] is not None
        assert fold["test_summary"] is not None
    assert body["notes"]


def test_not_enough_history_is_a_readable_refusal(auth_client, stub_market_data):
    """切不出來就說切不出來，不要回一份空報告。"""
    resp = auth_client.post("/api/backtests/walk-forward", json=_request(train_bars=5000))

    assert resp.status_code == 422, resp.text
    assert "不夠" in resp.json()["detail"]
