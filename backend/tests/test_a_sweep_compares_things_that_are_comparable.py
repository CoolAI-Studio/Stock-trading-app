"""參數掃描：同一支策略跑一整個參數網格，把結果排出來。

#34 的第一項。宣告式參數（`StrategyParams`）已經做好了，這是它的下一步。

＊ 這一組測的核心是「可比」，不是「跑得動」。

一張排名表的全部價值在於**列與列之間的差異只來自參數**。只要有任何一個其他變數
跟著動，那張表就從「哪組參數比較好」變成「哪組參數在哪批資料上比較好」——而後者
看起來一模一樣，沒有任何東西會說。

所以最重要的不變式是：**每一組參數看到的是同一批 K 棒。** 如果每組各抓一次，中間
遇到 provider 打嗝、快取過期、或上游做了一次除權調整，那一列就跟其他列不可比，而
使用者會照著那張表去挑參數。

＊ 第二件事：掃描是**過度配適的生產線**。

在一整個網格上挑最高的那一格，挑到的通常是雜訊。這不是可以用測試修掉的事，但它是
使用者一定要看到的話——票上的 walk-forward 就是為了這個而存在。所以結果裡要說。

＊ 第三件事：上限要說出來。

這個 repo 已經被「靜默截斷」咬過：`backtest.py` 要 20,000 根、provider 給五年、差
額被吞掉。同一個錯不可以在掃描上再犯一次——網格太大就明講砍了幾組，不要安靜地只
跑前幾組。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.enums import DataSource
from app.main import app
from app.services import param_sweep
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service

_START = datetime(2026, 1, 5, tzinfo=UTC)

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

# 只有 window=2 那組會跑完；其他組卡死。掃描不可以因此整場沒有結果。
ONE_COMBO_HANGS = """
class Strategy:
    def __init__(self):
        self.name = "partly_stuck"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.params = {"window": 2}

    def on_bar(self, bar) -> str:
        if self.params["window"] != 2:
            while True:
                pass
        return "HOLD"
"""


def _bars(count: int = 40) -> list[Bar]:
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


class _StubBarProvider:
    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int = 300) -> list[Bar]:
        return _bars() if symbol == "2330.TW" else []

    def get_quotes(self, symbols, **kwargs):
        return {}


@pytest.fixture
def stub_market_data():
    service = MarketDataService(providers={DataSource.YFINANCE: _StubBarProvider()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


@pytest.fixture(autouse=True)
def _short_budget(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STRATEGY_BACKTEST_TIMEOUT_SEC", 3.0)


def test_every_combination_sees_the_same_candles():
    """**這一條是這個功能的地基。**

    K 棒抓一次，所有組共用。每組各抓一次的話，中間任何一次 provider 打嗝、快取過
    期、或上游做了除權調整，都會讓某一列跟其他列不可比——而那張表看起來完全正
    常，使用者會照著它挑參數。
    """
    bars = _bars()
    counted: list[int] = []

    result = param_sweep.run(
        source_code=TUNABLE,
        bars=bars,
        grid={"window": [2, 3, 5]},
        on_bars_used=counted.append,
    )

    assert len(result.rows) == 3
    # 每一列都報告它是在幾根 K 棒上跑的，而那幾個數字必須一樣。
    assert len({row.summary.bars_tested for row in result.rows}) == 1, (
        "不同組看到的 K 棒數不一樣，那張排名表不可比"
    )
    assert counted == [len(bars)], f"K 棒被抓了 {len(counted)} 次，應該只有一次"


def test_the_rows_actually_differ_by_the_parameter():
    """參數要真的有生效。

    這一條看起來多餘，其實是這個功能最容易安靜壞掉的地方：如果參數沒有傳進去，每
    一組跑的都是原始碼的預設值，那張表會有三列**一模一樣的數字**——而那看起來像
    「這個參數沒什麼影響」，一個完全合理、完全錯誤的結論。
    """
    result = param_sweep.run(
        source_code=TUNABLE,
        bars=_bars(),
        grid={"window": [2, 10, 30]},
    )

    assert {row.params["window"] for row in result.rows} == {2, 10, 30}
    signals = [row.summary.signals for row in result.rows]
    assert len(set(signals)) > 1, f"三組參數跑出一模一樣的結果，參數大概沒傳進去：{signals}"


def test_one_bad_combination_does_not_lose_the_whole_sweep():
    """一組跑不完，其他組的結果要留下來。

    掃描是 N 倍的曝險：跑一次的時候一支壞策略只毀掉一次回測，掃描的時候它會毀掉
    整張表。而使用者送掃描的原因，往往正是他還不知道哪組參數是合理的。
    """
    result = param_sweep.run(
        source_code=ONE_COMBO_HANGS,
        bars=_bars(),
        grid={"window": [1, 2, 3]},
    )

    ok = [row for row in result.rows if row.error is None]
    bad = [row for row in result.rows if row.error is not None]

    assert len(ok) == 1, f"跑得完的那一組不見了：{[r.params for r in ok]}"
    assert ok[0].params["window"] == 2
    assert len(bad) == 2
    # 壞掉的那幾組要說得出原因，不是一格空白。
    assert all(row.error for row in bad)


def test_a_grid_too_big_says_so_instead_of_quietly_running_the_first_few():
    """靜默截斷是這個 repo 已經犯過的錯，不可以再犯一次。

    `backtest.py` 要 20,000 根、provider 給五年、差額被吞掉——結果是真的，但測的
    不是他要的區間。網格太大的時候安靜地只跑前幾組，是同一個錯換一個地方。
    """
    # 用**宣告過的**那個參數。原本我隨手寫了 a/b 兩個不存在的名字，結果它先撞上
    # 「參數沒宣告」那道檢查而不是截斷——測試紅了，但紅的原因不是我要測的那個。
    grid = {"window": list(range(2, 402))}  # 400 組

    result = param_sweep.run(source_code=TUNABLE, bars=_bars(), grid=grid)

    assert len(result.rows) <= param_sweep.MAX_COMBINATIONS
    assert result.truncated_note, "砍掉了組合卻沒有說"
    assert str(400) in result.truncated_note, f"沒說原本有幾組：{result.truncated_note}"


def test_the_result_warns_that_picking_the_best_row_is_how_you_overfit():
    """在一整個網格上挑最高的那一格，挑到的通常是雜訊。

    這件事沒辦法用程式擋掉，但它是使用者一定要看到的話——而票上的 walk-forward
    正是為了這個而存在。掃描交出一張排名表卻不說這件事，等於在教他做錯的事。
    """
    result = param_sweep.run(source_code=TUNABLE, bars=_bars(), grid={"window": [2, 3]})

    joined = " ".join(result.notes)
    assert joined, "掃描結果沒有任何提醒"
    assert "過度配適" in joined or "overfit" in joined.lower()


def test_a_parameter_the_source_never_declared_is_refused():
    """跟存策略那條路一致：宣告之外的名字不收。

    一個策略讀不到的參數，掃出來的每一列都會是同一個數字——而使用者會以為那是
    「這個參數沒有影響」。跟 _check_params 同一個道理，只是這裡錯一次會錯 N 列。
    """
    with pytest.raises(param_sweep.SweepError):
        param_sweep.run(
            source_code=TUNABLE,
            bars=_bars(),
            grid={"nonexistent_knob": [1, 2]},
        )


def test_an_empty_grid_is_refused_rather_than_silently_running_once():
    """空網格跑一次預設值，看起來像「掃描完成」而其實什麼都沒掃。"""
    with pytest.raises(param_sweep.SweepError):
        param_sweep.run(source_code=TUNABLE, bars=_bars(), grid={})


# --- 端點 --------------------------------------------------------------------


SWEEPABLE = TUNABLE.replace('self.name = "tunable"', 'self.name = "sweepable"')


def _sweep_request(**overrides) -> dict:
    payload = {
        "source_code": SWEEPABLE,
        "symbol": "2330.TW",
        "start": _START.isoformat(),
        "end": (_START + timedelta(days=39)).isoformat(),
        "grid": {"window": [2, 5]},
    }
    payload.update(overrides)
    return payload


def test_the_sweep_endpoint_needs_a_login(client):
    """稽查的硬性關卡：沒有帳號閘門的端點直接紅燈。

    掃描比單次回測貴 N 倍，所以它是這個 app 裡最不該裸奔的一個端點。
    """
    resp = client.post("/api/backtests/sweep", json=_sweep_request())

    assert resp.status_code in (401, 403), resp.status_code


def test_the_endpoint_reports_the_one_batch_of_candles_it_used(auth_client, stub_market_data):
    """回應要說出這張表是在哪一段、幾根 K 棒上跑出來的。

    這是「可比」在 HTTP 這一層的樣子：使用者看到的是一張表，而那張表底下有一個他
    看不見的前提——所有列共用同一批 K 棒。把它印出來，那個前提才變成他檢查得到的
    東西。
    """
    body = auth_client.post("/api/backtests/sweep", json=_sweep_request()).json()

    assert body["bars_total"] > 0
    assert body["first_bar_at"] and body["last_bar_at"]
    assert len(body["rows"]) == 2
    assert {row["params"]["window"] for row in body["rows"]} == {2, 5}


def test_the_endpoint_refuses_an_undeclared_parameter(auth_client, stub_market_data):
    resp = auth_client.post(
        "/api/backtests/sweep", json=_sweep_request(grid={"not_a_knob": [1, 2]})
    )

    assert resp.status_code == 422, resp.text
