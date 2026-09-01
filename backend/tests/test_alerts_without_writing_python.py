"""一則提醒，不用寫 Python 就設定得出來。

CLAUDE.md 把這件事寫成核心功能，不是加分項：

    不用寫 Python 就能設定的簡單價格提醒，是**核心功能**，不是加分項。

MEASURED BEFORE THIS EXISTED. 想要「台積電跌到 900 塊叫我」的唯一一條路是：
策略頁 → 新增 → 從範例挑 price_alert.py → **在一個程式碼編輯器裡**把
`self.buy_below = 950.0` 改掉 → 存檔 → 啟用。範例檔讓改動變成「改三個數字」，
但畫面上仍然是程式碼，而「改三個數字」和「不用寫程式」對這個使用者是兩件事。

另一條路是 AI（`/api/strategies/generate`），但那需要一把金鑰，而 CLAUDE.md
同樣寫著 AI 不能是設定流程的必需品。所以實際狀態是：要嘛寫 Python，要嘛付錢。

WHAT THIS BUILDS ON RATHER THAN REPLACES. 參數機制早就有了：策略可以宣告
`self.params`，擁有者不改程式碼就能覆寫。缺的只是「一份帶著中文說明的現成範本
清單」和「一條不用送出任何程式碼的建立路徑」。所以底下是同一個 worker、同一套
節流、同一條通知重送、同一個沙箱——那條路已經被測過幾百次。

每一條測試都是**實測**：範本真的編譯、真的餵 K 棒進去看它會不會叫。
讀程式碼確認「它應該會動」不算數。
"""

from datetime import UTC, datetime

import pytest

from app.services.market_data.base import Bar, Timeframe
from app.services.strategy_runtime import compile_strategy
from app.services.strategy_templates import TEMPLATES, get_template


def bar(close: float, *, high: float | None = None, low: float | None = None) -> Bar:
    return Bar(
        symbol="2330.TW",
        timeframe=Timeframe.DAY_1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1000.0,
    )


def feed(template_key: str, params: dict, closes: list[float]) -> list[str]:
    """Run a template over a price series and return every signal it gave."""
    template = get_template(template_key)
    loaded = compile_strategy(template.source, params=params)
    return [loaded.on_bar(bar(close)) for close in closes]


def feed_ticks(template_key: str, params: dict, prices: list[float]) -> list[str]:
    """同上，但走逐筆——到價提醒看的是**現在的價格**，不是日 K 的收盤價。"""
    template = get_template(template_key)
    loaded = compile_strategy(template.source, params=params)
    return [loaded.on_tick(price) for price in prices]


# --- 每一個範本本身 -------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.key)
def test_every_template_compiles_with_its_own_defaults(template):
    """A template that does not compile is a button that fails after the person
    has already filled in the form."""
    loaded = compile_strategy(template.source)

    # 兩種入口都是合法的，而**哪一種是對的由那個範本在講什麼決定**：到價提醒講的是
    # 「現在的價格」所以走逐筆，跌破均線講的是收盤價所以走 K 棒。這裡只驗「有一個入
    # 口而且編得起來」——選錯入口由底下那幾條各自守。
    assert loaded.entry_point in ("on_bar", "on_tick")


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.key)
def test_the_form_asks_for_exactly_what_the_code_reads(template):
    """兩個方向都要檢查，因為兩種錯法都會安靜地騙人：

    表單問了程式沒在用的欄位 → 他填了一個數字，然後什麼都沒發生。
    程式要的參數表單沒問  → 他拿到的是作者的預設值，而畫面上不會說。
    """
    declared = set(compile_strategy(template.source).declared_params)
    asked = {field.key for field in template.fields}

    assert asked == declared


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.key)
def test_every_template_says_what_it_is_for_in_the_owners_language(template):
    """這份清單是給不是工程師的人看的。沒有說明的選項等於沒有選項。"""
    assert template.title
    assert template.summary
    assert template.good_for
    for field in template.fields:
        assert field.label
        assert field.help


# --- 到價提醒：這個產品的核心功能 -----------------------------------------------------


def test_a_price_alert_fires_when_the_price_falls_to_the_number_you_typed():
    signals = feed_ticks("price_alert", {"buy_below": 900.0, "sell_above": 0.0}, [950.0, 899.0])

    assert signals == ["HOLD", "BUY"]


def test_and_when_it_rises_past_the_other_one():
    signals = feed_ticks("price_alert", {"buy_below": 0.0, "sell_above": 1200.0}, [1100.0, 1250.0])

    assert signals == ["HOLD", "SELL"]


def test_filling_in_only_one_side_is_a_complete_answer():
    """「跌到 900 叫我」是完整的需求。強迫他也想一個賣出價，是在問一個他沒有
    答案的問題。0 表示這一邊不用管。"""
    signals = feed_ticks("price_alert", {"buy_below": 900.0, "sell_above": 0.0}, [5000.0, 899.0])

    assert signals == ["HOLD", "BUY"]


def test_zero_does_not_mean_alert_on_everything():
    """A blank side must be inert, not a threshold of zero that every price is
    above -- that would notify on every single candle, forever."""
    signals = feed_ticks("price_alert", {"buy_below": 0.0, "sell_above": 0.0}, [1.0, 999.0, 0.5])

    assert set(signals) == {"HOLD"}


# --- 內建規則（方案 3 的四條） --------------------------------------------------------


def test_falling_through_the_moving_average_is_noticed():
    """跌破 20 日均線。前面 20 根撐住，第 21 根跌破。"""
    closes = [100.0] * 20 + [80.0]

    signals = feed("ma_break", {"window": 20}, closes)

    assert signals[-1] == "SELL"
    assert set(signals[:-1]) == {"HOLD"}


def test_a_new_high_is_noticed():
    """突破 60 日新高，用比較短的 window 測同一段邏輯。"""
    closes = [100.0] * 10 + [130.0]

    signals = feed("high_break", {"window": 10}, closes)

    assert signals[-1] == "BUY"


def test_a_drop_from_the_recent_peak_is_noticed():
    """從最近高點回落 10%。"""
    closes = [100.0, 120.0, 118.0, 100.0]

    signals = feed("drawdown", {"lookback": 20, "drop_pct": 10.0}, closes)

    assert signals[-1] == "SELL"
    assert signals[1] == "HOLD"  # 高點那一根不算回落


def test_an_oversold_reading_is_noticed():
    """RSI 低於 30：連續下跌會把它壓下去。"""
    closes = [100.0 - i for i in range(20)]

    signals = feed("rsi_oversold", {"period": 14, "threshold": 30.0}, closes)

    assert "BUY" in signals


def test_a_calm_market_says_nothing_at_all():
    """The failure mode that destroys an alerting product is not silence, it is
    noise: a rule that fires every day is one the owner mutes, and then the
    real one arrives muted too."""
    flat = [100.0] * 40

    for key, params in (
        ("ma_break", {"window": 20}),
        ("high_break", {"window": 10}),
        ("drawdown", {"lookback": 20, "drop_pct": 10.0}),
        ("rsi_oversold", {"period": 14, "threshold": 30.0}),
    ):
        assert set(feed(key, params, flat)) == {"HOLD"}, key


# --- 建立那條路：使用者不送出任何程式碼 -----------------------------------------------


def test_the_list_of_templates_is_readable_by_a_logged_in_owner(auth_client):
    body = auth_client.get("/api/strategies/templates").json()

    assert {item["key"] for item in body} == {t.key for t in TEMPLATES}
    assert all(item["title"] and item["summary"] for item in body)


def test_that_path_is_not_swallowed_by_the_one_that_takes_an_id(auth_client):
    """/api/strategies/{strategy_id} is declared in the same router and matches
    any string. Declared in the wrong order, 「templates」 becomes a strategy id
    and this endpoint silently 404s -- a trap this router already has three
    other literal paths sitting in."""
    assert auth_client.get("/api/strategies/templates").status_code == 200


def test_creating_one_needs_no_source_code_at_all(auth_client):
    response = auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "台積電到價",
            "symbol": "2330.TW",
            "params": {"buy_below": 900.0, "sell_above": 0.0},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["symbol"] == "2330.TW"


def test_what_it_makes_is_an_ordinary_strategy(auth_client):
    """引導做出來的東西不是隱形設定：它出現在策略清單裡，可以照常編輯和刪除。"""
    auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "台積電到價",
            "symbol": "2330.TW",
            "params": {"buy_below": 900.0, "sell_above": 0.0},
        },
    )

    names = [item["name"] for item in auth_client.get("/api/strategies").json()]

    assert "台積電到價" in names


def test_it_never_places_an_order(auth_client):
    """這是提醒系統，不是下單系統。表單建出來的東西只會通知。"""
    created = auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "只提醒",
            "symbol": "2330.TW",
            "params": {"buy_below": 900.0, "sell_above": 0.0},
        },
    ).json()

    detail = auth_client.get(f"/api/strategies/{created['id']}").json()

    assert detail["alert_only"] is True


def test_it_is_switched_on_the_moment_it_is_created(auth_client):
    """「跌到 900 叫我」是一個沒有歧義的意圖。

    A hand-written strategy starts inactive on purpose -- the author wants to
    read it again before it trades. A form-built alert has no such moment:
    somebody typed a price and pressed a button. Leaving it off would give
    them an alarm that never rings, and nothing on the screen would say why.
    """
    created = auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "台積電到價",
            "symbol": "2330.TW",
            "params": {"buy_below": 900.0, "sell_above": 0.0},
        },
    ).json()

    assert created["is_active"] is True


def test_the_numbers_he_typed_are_what_it_runs_with(auth_client):
    created = auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "台積電到價",
            "symbol": "2330.TW",
            "params": {"buy_below": 900.0, "sell_above": 1200.0},
        },
    ).json()

    detail = auth_client.get(f"/api/strategies/{created['id']}").json()

    assert detail["params"]["buy_below"] == 900.0
    assert detail["params"]["sell_above"] == 1200.0


def test_an_unknown_template_is_refused_in_words(auth_client):
    response = auth_client.post(
        "/api/strategies/from-template",
        json={"template": "no-such-thing", "name": "x", "symbol": "2330.TW", "params": {}},
    )

    assert response.status_code == 404
    assert "no-such-thing" in response.text


def test_a_parameter_that_is_not_a_number_says_which_one(auth_client):
    """「失敗」不是訊息。他要知道是哪一格。"""
    response = auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "x",
            "symbol": "2330.TW",
            "params": {"buy_below": "九百"},
        },
    )

    assert response.status_code == 422
    assert "buy_below" in response.text


def test_a_symbol_that_cannot_be_priced_is_refused_the_same_way_as_anywhere_else(auth_client):
    """同一條路，不是繞過去的第二條：既有的代號／行情來源檢查照樣適用。"""
    response = auth_client.post(
        "/api/strategies/from-template",
        json={
            "template": "price_alert",
            "name": "x",
            "symbol": "BTCUSDT",
            "data_source": "yfinance",
            "params": {"buy_below": 1.0, "sell_above": 0.0},
        },
    )

    assert response.status_code == 422


def test_nobody_else_can_read_the_template_list(client):
    """It is not secret, but nothing in this app answers a stranger without a
    reason, and this one has none."""
    assert client.get("/api/strategies/templates").status_code == 401


# --- 「跌到 900 叫我」要在他跌到 900 的時候叫他 -------------------------------
#
# CLAUDE.md：「不用寫 Python 就能設定的簡單價格提醒，是**核心功能**，不是加分項。」
#
# 而到價提醒原本是 `timeframe = "1d"` ＋ 只有 `on_bar`，也就是**一天只看一次，而且看
# 的是收盤價**。盤中摸到 890 又拉回 910 收盤，他什麼都收不到——那跟「跌到 900 叫我」
# 這句話直接矛盾，而畫面上沒有一個字提過這件事。
#
# 「跌破均線」那幾個範本用日收盤是**對的**（那本來就是收盤價的概念），所以只有這一個
# 要換條路走。


def test_a_price_alert_reacts_to_the_live_price_not_the_daily_close():
    """他說「跌到 900 叫我」，就要在價格是 900 的那一刻叫他。"""
    template = get_template("price_alert")
    loaded = compile_strategy(template.source, params={"buy_below": 900.0, "sell_above": 0.0})

    assert loaded.entry_point == "on_tick", (
        "到價提醒還是 on_bar——那表示它一天只看一次收盤價，盤中跌到 900 不會通知"
    )
    assert loaded.on_tick(950.0) == "HOLD"
    assert loaded.on_tick(899.0) == "BUY"


def test_the_intraday_dip_that_recovers_still_alerts():
    """**這一條就是差別本身。**

    盤中跌到 890、收盤拉回 910。用日 K 收盤價看，這一天什麼都沒發生；而他要的正是
    那一刻。
    """
    template = get_template("price_alert")
    loaded = compile_strategy(template.source, params={"buy_below": 900.0, "sell_above": 0.0})

    intraday = [950.0, 920.0, 890.0, 905.0, 910.0]

    assert "BUY" in [loaded.on_tick(price) for price in intraday]


def test_the_other_templates_still_read_the_close():
    """跌破均線、新高、回檔、超賣——這幾個講的本來就是收盤價的概念。

    把它們一起改成逐筆，會讓「收盤價跌破均線」變成「盤中摸到均線就叫」，那是另一個
    完全不同的策略，而且會吵得多。
    """
    # 鍵名從 TEMPLATES 拿，不要用背的：我第一版猜了 new_high／pullback／oversold，
    # 而真正的名字是 high_break／drawdown／rsi_oversold，get_template 回 None，
    # 測試炸在 AttributeError 而不是斷言失敗——一個看不出真正原因的紅燈。
    for key in [t.key for t in TEMPLATES if t.key != "price_alert"]:
        loaded = compile_strategy(get_template(key).source)
        assert loaded.entry_point == "on_bar", f"{key} 不應該改成逐筆"
