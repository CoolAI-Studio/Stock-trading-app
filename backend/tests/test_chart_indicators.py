"""Indicators on the chart, computed by the code the strategies already use.

The owner's words: 「沒有任何指標可以選擇，重點就是要那些指標才有辦法下策略跟回測」.
There are 40 indicators in the runtime and they were reachable only by writing
Python -- the catalogue endpoint served them as a reference list for somebody
typing code, which for this app's audience is the same as not having them.

THE CONSTRAINT. If the chart draws one moving average and a strategy trades a
different one, the owner is looking at a picture of something that is not
happening. That is worse than having no indicators. It is satisfied by
construction, not by discipline: `spec.fn` is the same object the sandbox hands
to strategies, so there is no second implementation to keep in step -- and there
must never be one in TypeScript.

MEASURED, not assumed (300-bar synthetic walk in the 68-101 band, all 40
indicators actually computed):
  every series is exactly as long as its input, with leading Nones for warm-up;
  NO indicator produces NaN or Inf anywhere;
  pivot_points takes three scalars and returns seven -- unchartable;
  39 indicators over 250 bars costs 87 ms, over 1000 bars 542 ms;
  ema/kama/rsi/atr/parabolic_sar/sma converge (0.0000% drift between a 250-bar
  and a 1000-bar window) while obv drifts 71.5%, because it is a running sum.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import chart_indicators, indicator_panes
from app.services.market_data.base import Bar, Timeframe


def _bars(count: int = 120) -> list[Bar]:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    out = []
    price = 100.0
    for i in range(count):
        price += 1.0 if i % 3 else -0.5
        out.append(
            Bar(
                symbol="TEST",
                timeframe=Timeframe.DAY_1,
                timestamp=start + timedelta(days=i),
                open=price - 0.2,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=1_000_000.0 + i,
            )
        )
    return out


# --- the same maths the strategies run ------------------------------------------


def test_the_chart_calls_the_very_function_the_sandbox_hands_to_strategies():
    """Not 「the same formula」 -- the same object. A TypeScript moving average
    would be a second implementation, and the first time the two disagreed
    nobody would find out from the screen."""
    from app.services.indicators import catalogue, indicator_namespace

    by_name = {spec.name: spec for spec in catalogue()}

    assert indicator_namespace().sma is by_name["sma"].fn


def test_a_moving_average_matches_what_a_strategy_would_compute():
    from app.services.indicators import catalogue

    bars = _bars()
    sma = next(spec for spec in catalogue() if spec.name == "sma")
    expected = sma.fn([float(b.close) for b in bars], period=20)

    series = chart_indicators.compute(bars, [{"name": "sma", "params": {"period": 20}}])[0]

    assert series.points[-1].value == expected[-1]


# --- what arrives on the wire -----------------------------------------------------


def test_every_point_carries_its_own_timestamp():
    """Not a positional array. Zipping by index against the chart's candles is
    how a moving average ends up drawn one bar sideways with nothing on screen
    saying so."""
    bars = _bars()
    series = chart_indicators.compute(bars, [{"name": "sma", "params": {"period": 5}}])[0]

    assert series.points[-1].time == bars[-1].timestamp


def test_the_warm_up_is_omitted_rather_than_sent_as_null():
    """Every indicator returns a list as long as its input with leading Nones.
    A null is not a point a line renderer can draw."""
    bars = _bars()
    series = chart_indicators.compute(bars, [{"name": "sma", "params": {"period": 20}}])[0]

    assert len(series.points) < len(bars)
    assert all(isinstance(point.value, float) for point in series.points)


def test_an_indicator_with_several_outputs_comes_back_as_several_series():
    bars = _bars()
    out = chart_indicators.compute(bars, [{"name": "macd"}])

    assert {s.key for s in out} == {"macd", "signal", "histogram"}


def test_the_authors_defaults_apply_when_nothing_is_supplied():
    bars = _bars()

    assert chart_indicators.compute(bars, [{"name": "rsi"}])[0].points


def test_no_bars_is_no_series_rather_than_a_crash():
    assert chart_indicators.compute([], [{"name": "sma"}]) == []


# --- which axis each one goes on ---------------------------------------------------


def test_a_moving_average_shares_the_candles_axis():
    assert indicator_panes.pane_for("sma") == indicator_panes.PRICE


def test_an_oscillator_gets_its_own():
    assert indicator_panes.pane_for("rsi") == indicator_panes.OWN


def test_something_in_price_units_that_is_not_a_price_level_still_gets_its_own():
    """atr is measured in dollars but is a WIDTH, not a level. Any rule that
    reads 「comparable to price」 off a number puts it on the price axis and pins
    it to the floor."""
    assert indicator_panes.pane_for("atr") == indicator_panes.OWN
    assert indicator_panes.pane_for("stdev") == indicator_panes.OWN


def test_the_two_indicators_that_mix_scales_are_split_per_output():
    """bollinger_bands returns three prices AND a 0-1 ratio from one call;
    supertrend returns three prices AND a ±1 flag. Routing either whole is what
    flattens the candles."""
    assert indicator_panes.pane_for("bollinger_bands", "upper") == indicator_panes.PRICE
    assert indicator_panes.pane_for("bollinger_bands", "percent_b") == indicator_panes.OWN
    assert indicator_panes.pane_for("supertrend", "supertrend") == indicator_panes.PRICE
    assert indicator_panes.pane_for("supertrend", "direction") == indicator_panes.OWN


def test_two_outputs_in_one_pane_that_do_not_share_a_scale_are_told_apart():
    """MEASURED over a 300-bar walk, every multi-output oscillator in the
    catalogue: adx, aroon, macd, trix, stoch_rsi, stochastic and tsi all have
    outputs on comparable scales -- macd and its signal line are MEANT to be
    read against each other, and separating them would destroy the only thing
    anybody looks at them for.

    bollinger_bands is the single exception: bandwidth runs 4.5-25 and
    percent_b runs -0.2-1.2. On one shared axis percent_b is a flat line on the
    floor, which is the same failure the pane map exists to prevent, one
    magnitude smaller. So the scale is declared too, and only where it
    genuinely differs.
    """
    assert indicator_panes.scale_for("bollinger_bands", "bandwidth") != indicator_panes.scale_for(
        "bollinger_bands", "percent_b"
    )


def test_the_lines_that_are_meant_to_be_compared_keep_one_scale():
    for key in ("macd", "signal", "histogram"):
        assert indicator_panes.scale_for("macd", key) == indicator_panes.scale_for("macd", "macd")


def test_every_output_has_a_scale_group_and_a_single_output_needs_no_special_case():
    from app.services.indicators import catalogue

    for spec in catalogue():
        if spec.name in indicator_panes.UNCHARTABLE:
            continue
        for key in spec.keys or ("",):
            assert indicator_panes.scale_for(spec.name, key)


def test_the_catalogue_says_which_scale_each_output_uses(auth_client):
    body = auth_client.get("/api/market/indicators/available").json()

    bb = next(e for e in body["indicators"] if e["name"] == "bollinger_bands")
    scales = {o["key"]: o["scale"] for o in bb["outputs"]}
    assert scales["bandwidth"] != scales["percent_b"]


def test_obv_does_not_go_anywhere_near_the_price_axis():
    """Measured at ±7.6e7 against candles in the double digits. On the price
    axis the candles become a flat line."""
    assert indicator_panes.pane_for("obv") == indicator_panes.OWN


def test_every_indicator_in_the_catalogue_has_a_declared_axis():
    """THIS TEST IS THE ENFORCEMENT. The axis cannot be derived -- not from
    category (trend holds both sma and macd), not from result type (sma and rsi
    are both plain series), not from the value range (atr is in price units and
    is not a price). So it is declared, and the 41st indicator must not be able
    to land on the price axis by default."""
    from app.services.indicators import catalogue

    missing = []
    for spec in catalogue():
        if spec.name in indicator_panes.UNCHARTABLE:
            continue
        for key in spec.keys or ("",):
            try:
                indicator_panes.pane_for(spec.name, key)
            except KeyError:
                missing.append(f"{spec.name}.{key}" if key else spec.name)

    assert not missing, f"no declared axis for: {', '.join(missing)}"


def test_an_undeclared_indicator_raises_rather_than_guessing():
    with pytest.raises(KeyError):
        indicator_panes.pane_for("something_new")


# --- what it refuses ----------------------------------------------------------------


def test_the_one_that_cannot_be_drawn_is_refused_by_name():
    """pivot_points takes three SCALARS and returns seven. There is no series
    for a line renderer, and offering it would be offering a choice that cannot
    work."""
    assert "pivot_points" in indicator_panes.UNCHARTABLE

    with pytest.raises(chart_indicators.IndicatorRequestError):
        chart_indicators.compute(_bars(), [{"name": "pivot_points"}])


def test_it_is_not_offered_in_the_first_place():
    assert "pivot_points" not in {entry["name"] for entry in indicator_panes.chartable()}


def test_an_unknown_indicator_is_refused():
    with pytest.raises(chart_indicators.IndicatorRequestError):
        chart_indicators.compute(_bars(), [{"name": "not_a_thing"}])


def test_a_parameter_that_makes_the_maths_raise_is_a_sentence_not_a_500():
    """A period longer than the window, a negative length. The library raises
    and the page can only act on words."""
    with pytest.raises(chart_indicators.IndicatorRequestError):
        chart_indicators.compute(_bars(10), [{"name": "sma", "params": {"period": -5}}])


def test_a_float_where_a_whole_number_was_declared_is_refused():
    """JSON has one number type, so 20.5 arrives looking plausible and then
    makes range() raise deep inside the library."""
    with pytest.raises(chart_indicators.IndicatorRequestError) as caught:
        chart_indicators.compute(_bars(), [{"name": "sma", "params": {"period": 20.5}}])

    # The MESSAGE, not just the type. IndicatorRequestError subclasses
    # ValueError, so a bare `except ValueError` around the coercion swallows
    # this sentence and replaces it with 「不是數字」 -- which is false, 20.5 is
    # a number, and it sends the reader looking for the wrong mistake.
    assert "整數" in str(caught.value)


def test_a_parameter_that_is_not_a_number_at_all_says_that_instead():
    with pytest.raises(chart_indicators.IndicatorRequestError) as caught:
        chart_indicators.compute(_bars(), [{"name": "sma", "params": {"period": "二十"}}])

    assert "數字" in str(caught.value)


def test_a_period_longer_than_the_window_is_a_sentence_not_a_blank_line():
    """MEASURED: sma(period=9999) over 250 bars does not raise. It returns 250
    Nones, so every point is dropped as warm-up and the answer is a series with
    nothing in it -- an empty line on the chart and not one word about why.

    That is the failure this whole app is built to refuse. Somebody who types a
    number too big has to be told so."""
    with pytest.raises(chart_indicators.IndicatorRequestError) as caught:
        chart_indicators.compute(_bars(250), [{"name": "sma", "params": {"period": 9999}}])

    assert "250" in str(caught.value)


def test_the_endpoint_turns_that_into_a_sentence_the_page_can_show(auth_client):
    from app.enums import DataSource
    from app.main import app
    from app.services.market_data.service import MarketDataService, get_market_data_service

    bars = _bars(120)

    class _Stub:
        data_source = DataSource.YFINANCE

        def get_quotes(self, symbols):
            return {}

        def get_bars(self, symbol, timeframe, limit):
            return bars[-limit:]

    service = MarketDataService(providers={DataSource.YFINANCE: _Stub()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        resp = auth_client.post(
            "/api/market/indicators",
            json={"symbol": "2330.TW", "indicators": [{"name": "sma", "params": {"period": 9999}}]},
        )
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert resp.status_code == 422
    assert "sma" in resp.json()["detail"]


def test_a_series_that_does_not_line_up_with_the_bars_is_refused(monkeypatch):
    """Every indicator returns a list exactly as long as its input -- measured
    across all forty. If one ever stops, zip() aligns the two from the START
    and shifts the whole line sideways. A plausible, well-formed, WRONG chart
    is the failure this app treats as worse than a blank one."""
    import dataclasses

    from app.services.indicators import catalogue

    # COPIED, never mutated in place. `catalogue()` hands back the very
    # IndicatorSpec objects the sandbox namespace was built from, so
    # object.__setattr__ on one of them replaces sma for the rest of the
    # process -- which is how this test first broke two unrelated tests that
    # ran after it.
    def short(*args, **kwargs):
        return [
            dataclasses.replace(spec, fn=lambda values, period=20: [1.0, 2.0])
            if spec.name == "sma"
            else spec
            for spec in catalogue(*args, **kwargs)
        ]

    monkeypatch.setattr(chart_indicators, "catalogue", short)

    with pytest.raises(chart_indicators.IndicatorRequestError):
        chart_indicators.compute(_bars(), [{"name": "sma"}])


def test_a_value_that_is_not_a_real_number_is_dropped_rather_than_serialised():
    """Python's json writes NaN and inf as the bare tokens `NaN` and
    `Infinity`, which are not JSON -- JSON.parse in the browser throws and the
    whole response is lost over one bad point.

    MEASURED HONESTLY: no indicator produces one from flat prices, zero prices
    or extreme swings. Six of the volume indicators do at volumes around 1e308,
    which no provider returns. So this is not a bug being fixed; it is one
    condition standing between a future arithmetic edge and an illegible
    failure.
    """
    import dataclasses

    from app.services.indicators import catalogue

    def poisoned(*args, **kwargs):
        return [
            dataclasses.replace(
                spec, fn=lambda values, period=20: [float("nan")] * (len(values) - 1) + [7.0]
            )
            if spec.name == "sma"
            else spec
            for spec in catalogue(*args, **kwargs)
        ]

    import unittest.mock

    with unittest.mock.patch.object(chart_indicators, "catalogue", poisoned):
        series = chart_indicators.compute(_bars(), [{"name": "sma"}])

    assert [point.value for point in series[0].points] == [7.0]


def test_asking_for_too_many_at_once_is_refused():
    """This runs in the same process as the market loop. A page that refetches
    on focus must not be able to spend half a second of it on a whim."""
    with pytest.raises(chart_indicators.IndicatorRequestError):
        chart_indicators.compute(
            _bars(), [{"name": "sma"}] * (chart_indicators.MAX_INDICATORS_PER_REQUEST + 1)
        )


def test_a_bar_with_no_volume_does_not_break_the_volume_indicators():
    """Bar.volume is optional and the provider's NaN guard covers OHLC only, so
    a row padded over a halt really does arrive with none."""
    bars = _bars()
    bars[5] = Bar(
        symbol="TEST",
        timeframe=Timeframe.DAY_1,
        timestamp=bars[5].timestamp,
        open=bars[5].open,
        high=bars[5].high,
        low=bars[5].low,
        close=bars[5].close,
        volume=None,
    )

    assert chart_indicators.compute(bars, [{"name": "obv"}])[0].points


# --- every chartable indicator actually computes --------------------------------------


def test_all_of_them_can_be_drawn_over_real_looking_candles():
    """Sampling four and shipping is how the 「it only broke for the volume
    ones」 bug gets written. Every indicator the picker offers is asked for
    here, with the author's own defaults."""
    import math

    bars = _bars(300)
    failed = []
    for entry in indicator_panes.chartable():
        try:
            series = chart_indicators.compute(bars, [{"name": entry["name"]}])
        except Exception as exc:  # noqa: BLE001 -- the report is the point
            failed.append(f"{entry['name']}: {type(exc).__name__} {exc}")
            continue
        if not series:
            failed.append(f"{entry['name']}: produced no series")
            continue
        # 「Did not raise」 is not the same as 「can be drawn」. An indicator that
        # returns a series of nothing, or one whose values cannot survive the
        # trip through JSON, is a blank line on the chart with no error
        # anywhere -- which is the failure this test exists to catch.
        for item in series:
            if not item.points:
                failed.append(f"{entry['name']}.{item.key}: no points")
            elif not all(math.isfinite(point.value) for point in item.points):
                failed.append(f"{entry['name']}.{item.key}: not a finite number")
            elif item.points != sorted(item.points, key=lambda point: point.time):
                failed.append(f"{entry['name']}.{item.key}: out of time order")

    assert not failed, "; ".join(failed)


def test_the_groups_are_labelled_in_chinese_not_as_enum_values():
    """CLAUDE.md: the reader is not an engineer. 「trend」 as a group heading is
    the enum leaking onto the screen."""
    sma = next(e for e in indicator_panes.chartable() if e["name"] == "sma")

    assert sma["category_label"] != sma["category"]
    assert sma["category_label"].strip()


def test_the_picker_lists_the_tuning_knobs_but_not_the_bar_columns():
    """`closes` and `highs` come from the candles. Offering them as fields
    would ask somebody to type a price series into a box."""
    sma = next(e for e in indicator_panes.chartable() if e["name"] == "sma")

    names = {p["name"] for p in sma["params"]}
    assert "period" in names
    assert "values" not in names


# --- through the API --------------------------------------------------------------


def test_the_endpoint_needs_a_login(client):
    resp = client.post("/api/market/indicators", json={"symbol": "AAPL", "indicators": []})

    assert resp.status_code == 401


def test_the_endpoint_returns_points_for_the_same_bars_the_chart_drew(auth_client):
    from app.enums import DataSource
    from app.main import app
    from app.services.market_data.service import MarketDataService, get_market_data_service

    bars = _bars(120)

    class _Stub:
        data_source = DataSource.YFINANCE

        def get_quotes(self, symbols):
            return {}

        def get_bars(self, symbol, timeframe, limit):
            return bars[-limit:]

    service = MarketDataService(providers={DataSource.YFINANCE: _Stub()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        resp = auth_client.post(
            "/api/market/indicators",
            json={"symbol": "2330.TW", "indicators": [{"name": "sma", "params": {"period": 20}}]},
        )
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["series"][0]["name"] == "sma"
    assert body["series"][0]["pane"] == "price"
    assert body["series"][0]["points"]


def test_the_endpoint_refuses_a_symbol_that_cannot_price(auth_client):
    resp = auth_client.post(
        "/api/market/indicators",
        json={"symbol": "台積電", "indicators": [{"name": "sma"}]},
    )

    assert resp.status_code == 422
    # The SYMBOL's problem specifically. FastAPI answers 422 for any body that
    # fails validation, so a bare status check would stay green if this
    # endpoint started rejecting the request for an entirely different reason.
    assert "台積電" in str(resp.json()["detail"])


def test_the_catalogue_endpoint_says_which_axis_each_one_needs(auth_client):
    """The picker cannot lay out panes without it, and guessing on the client
    would be the second implementation this whole design refuses."""
    body = auth_client.get("/api/market/indicators/available").json()

    by_name = {entry["name"]: entry for entry in body["indicators"]}
    assert by_name["sma"]["outputs"][0]["pane"] == "price"
    assert by_name["rsi"]["outputs"][0]["pane"] == "own"
