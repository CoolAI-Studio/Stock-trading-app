"""The symbol that arrives from outside, with nobody there to correct it.

TradingView's {{ticker}} placeholder expands to the ticker WITHOUT its
exchange: a chart on TWSE:2330 sends 「2330」. The app's own setup panel printed
a template using {{ticker}} and then, in the next sentence, told the owner that
Taiwanese symbols must look like 2330.TW -- an instruction that contradicts
itself, because that placeholder cannot produce that format.

And schemas/webhook.py accepted `symbol: str` with no validation at all, so a
bare 「2330」 went straight through to an order. Yahoo resolves a bare 2330 to an
unrelated Japanese OTC company, so it PRICES -- the wrong company's price,
acted on with complete confidence. The watchlist and the strategy form were
taught to refuse this today; the one input that comes from outside was not.

## Why this one resolves instead of refusing

Everywhere a human is present, the rule is 「suggest, never substitute」: show
the candidates and let them choose, because a silent substitution can point at
the wrong company. A webhook has nobody present. The only choices are to
resolve it or to drop the alert.

Resolving is safe HERE, and specifically here, because it is a lookup with a
unique answer rather than a guess: the bundled registry holds 1985 Taiwanese
codes with ZERO collisions between the listed and OTC boards (asserted below,
so a future refresh that introduces one fails loudly), and within the markets
this app models a purely numeric code cannot be a US ticker or a Binance pair.
That is exactly what Yahoo gets wrong -- it searches every market it knows.

Silent it is not: the adjustment is recorded on the webhook's audit row, and
the setup panel now prints a template that does not need adjusting.
"""

from app.config import settings
from app.models.order import Order
from app.models.webhook import TradingViewWebhookLog
from app.services import symbol_search


def _alert(**kw) -> dict:
    body = {
        "secret": settings.TV_WEBHOOK_SECRET,
        "symbol": "2330.TW",
        "action": "buy",
        "quantity": 1000,
        "price": 1000,
    }
    body.update(kw)
    return body


def _post(client, body: dict):
    return client.post("/api/webhooks/tradingview", json=body)


# --- the fact the decision rests on -----------------------------------------


def test_a_taiwanese_code_has_exactly_one_answer_in_the_registry():
    """The whole justification for resolving rather than refusing. If a future
    refresh of tw_listings.json ever introduced a collision, resolving would
    silently become a guess -- so it fails here instead."""
    codes = [row["code"] for row in symbol_search._listings()]

    assert len(codes) == len(set(codes)), "a code appears on both boards; resolving is now a guess"


# --- a bare code from TradingView ------------------------------------------


def test_a_bare_taiwanese_code_becomes_the_qualified_symbol(auth_client, client, db_session):
    """{{ticker}} on a TWSE:2330 chart sends 「2330」. Left alone, Yahoo prices a
    Japanese company under that number."""
    _post(client, _alert(symbol="2330", id="a1"))

    order = db_session.query(Order).one()
    assert order.symbol == "2330.TW"


def test_an_otc_code_gets_the_TWO_suffix(auth_client, client, db_session):
    _post(client, _alert(symbol="6488", id="a2"))

    assert db_session.query(Order).one().symbol == "6488.TWO"


def test_the_adjustment_is_recorded_so_it_is_not_silent(auth_client, client, db_session):
    """Resolving without saying so would be the substitution this app refuses
    everywhere a human is present."""
    _post(client, _alert(symbol="2330", id="a3"))

    log = db_session.query(TradingViewWebhookLog).one()
    assert log.note and "2330.TW" in log.note, log.note


def test_an_already_correct_symbol_is_left_alone_and_unremarked(auth_client, client, db_session):
    """A note on every single alert would stop being read."""
    _post(client, _alert(symbol="2330.TW", id="a4"))

    log = db_session.query(TradingViewWebhookLog).one()
    assert db_session.query(Order).one().symbol == "2330.TW"
    assert log.note is None


def test_a_us_ticker_passes_through_untouched(auth_client, client, db_session):
    """{{ticker}} is exactly right for US charts, and always was."""
    _post(client, _alert(symbol="AAPL", id="a5"))

    assert db_session.query(Order).one().symbol == "AAPL"


def test_a_lowercase_symbol_is_normalised(auth_client, client, db_session):
    _post(client, _alert(symbol="2330.tw", id="a6"))

    assert db_session.query(Order).one().symbol == "2330.TW"


# --- what still gets refused ------------------------------------------------


def test_a_company_name_is_refused_rather_than_guessed(auth_client, client, db_session):
    """A Chinese name has no unique answer -- 「台積」 matches several companies --
    so this is the case where refusing is right even with nobody present."""
    resp = _post(client, _alert(symbol="台積電", id="a7"))

    # 200 with ok:false is deliberate throughout this router -- TradingView
    # disables a webhook that keeps returning errors, and a disabled webhook is
    # every future alert lost, not just this one.
    assert resp.json()["ok"] is False
    assert db_session.query(Order).count() == 0


def test_the_refusal_is_recorded_with_a_usable_reason(auth_client, client, db_session):
    """A webhook that silently does nothing is indistinguishable from one that
    never arrived, and this page is the only place the owner can find out."""
    _post(client, _alert(symbol="台積電", id="a8"))

    log = db_session.query(TradingViewWebhookLog).one()
    assert log.order_id is None
    assert "代號" in (log.error or ""), log.error


def test_an_unknown_numeric_code_is_refused_not_invented(auth_client, client, db_session):
    """9999 is not in the registry. Appending .TW anyway would manufacture a
    symbol that prices as nothing -- or, worse, as something."""
    resp = _post(client, _alert(symbol="9999", id="a9"))

    assert resp.json()["ok"] is False
    assert db_session.query(Order).count() == 0


# --- the instructions that caused it ----------------------------------------


def test_the_panel_no_longer_contradicts_itself(auth_client):
    """It printed 「symbol": "{{ticker}}」 and then, in the next sentence, told
    the owner TW must look like 2330.TW -- a template that cannot produce what
    the sentence demands. The old sentence is what has to be gone."""
    notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "台股要確認是 2330.TW 這種格式" not in notes


def test_the_panel_says_what_the_placeholder_actually_sends_for_taiwan(auth_client):
    notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "四碼" in notes, notes
    assert "自動對應" in notes, notes
    assert ".TWO" in notes, "the OTC board needs a different suffix and must be mentioned"


def test_the_panel_says_where_to_see_what_it_was_mapped_to(auth_client):
    """An automatic mapping the owner cannot inspect is exactly the silent
    substitution this app refuses everywhere else."""
    notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "收件紀錄" in notes


def test_the_panel_says_a_bad_symbol_is_refused_rather_than_guessed(auth_client):
    notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "擋下來" in notes or "不會建立" in notes, notes


# --- the market the chart was on -------------------------------------------
#
# THE BUG THIS SECTION EXISTS FOR, and it was self-inflicted. Resolving a bare
# numeric code from the registry alone reproduced the very failure the registry
# was built to prevent: Japanese TSE codes are four digits in the same numeric
# band as Taiwanese ones, so an alert from a TSE:4502 chart (Takeda) resolved to
# 4502.TWO (健信) -- the wrong company, with an audit note confidently asserting
# the mapping was right. TSE:6902 (Denso) -> 6902.TW, HKEX:1810 (Xiaomi) ->
# 1810.TW. Structural, not a coincidence of sampling.
#
# TradingView's {{exchange}} placeholder is the missing context. It is not sent
# unless the owner puts it in the alert message, so its ABSENCE cannot be
# treated as permission to skip anything -- the legacy path keeps working
# exactly as before, and says out loud what it assumed.


def test_a_japanese_chart_is_refused_rather_than_mapped_to_a_taiwanese_company(
    auth_client, client, db_session
):
    """TSE:4502 is Takeda. 4502 is also 健信 in Taiwan. Without the exchange
    there is no way to tell; with it there is no excuse."""
    _post(client, _alert(symbol="4502", exchange="TSE", id="x1"))

    assert db_session.query(Order).count() == 0
    log = db_session.query(TradingViewWebhookLog).one()
    assert "TSE" in (log.error or ""), log.error


def test_a_hong_kong_chart_is_refused_too(auth_client, client, db_session):
    _post(client, _alert(symbol="1810", exchange="HKEX", id="x2"))

    assert db_session.query(Order).count() == 0


def test_a_taiwanese_exchange_resolves_as_before(auth_client, client, db_session):
    _post(client, _alert(symbol="2330", exchange="TWSE", id="x3"))

    assert db_session.query(Order).one().symbol == "2330.TW"


def test_the_otc_exchange_picks_the_TWO_suffix(auth_client, client, db_session):
    _post(client, _alert(symbol="6488", exchange="TPEX", id="x4"))

    assert db_session.query(Order).one().symbol == "6488.TWO"


def test_a_delayed_feed_suffix_is_stripped(auth_client, client, db_session):
    """Taiwan data on TradingView's free plan is 15 minutes delayed, and the
    docs say a delayed symbol's exchange ends in _DL or _DLY -- TWSE_DLY:2330
    is a real TradingView symbol. Matching only the bare form would miss every
    chart the owner actually has."""
    _post(client, _alert(symbol="2330", exchange="TWSE_DLY", id="x5"))

    assert db_session.query(Order).one().symbol == "2330.TW"


def test_a_us_exchange_passes_the_ticker_through(auth_client, client, db_session):
    _post(client, _alert(symbol="AAPL", exchange="NASDAQ_DLY", id="x6"))

    assert db_session.query(Order).one().symbol == "AAPL"


def test_an_exchange_the_app_does_not_model_is_refused_by_name(auth_client, client, db_session):
    """Refusing something we cannot price is right; silently pricing something
    else under that number is what this whole file is about."""
    _post(client, _alert(symbol="ABC", exchange="LSE", id="x7"))

    log = db_session.query(TradingViewWebhookLog).one()
    assert db_session.query(Order).count() == 0
    assert "LSE" in (log.error or "")


# --- the legacy path, where no exchange is sent -----------------------------


def test_without_an_exchange_it_still_resolves_so_existing_alerts_keep_working(
    auth_client, client, db_session
):
    """Every alert configured before today sends no exchange. Refusing those
    would drop every one of them, which is this product's critical failure --
    strictly worse than the collision it would be guarding against."""
    _post(client, _alert(symbol="2330", id="y1"))

    assert db_session.query(Order).one().symbol == "2330.TW"


def test_the_legacy_note_admits_what_it_assumed(auth_client, client, db_session):
    """The old note said 「已依上市櫃清單對應到 2330.TW」 as a statement of fact.
    Without the exchange it is an assumption, and it has to read like one."""
    _post(client, _alert(symbol="2330", id="y2"))

    note = db_session.query(TradingViewWebhookLog).one().note or ""
    assert "假設" in note or "台股圖表" in note, note


def test_an_exchange_carrying_alert_gets_no_such_caveat(auth_client, client, db_session):
    """When the chart told us its market there is nothing being assumed."""
    _post(client, _alert(symbol="2330", exchange="TWSE", id="y3"))

    note = db_session.query(TradingViewWebhookLog).one().note or ""
    assert "假設" not in note, note


def test_an_etf_resolves_now_that_the_registry_carries_them(auth_client, client, db_session):
    """0050 is among the most traded instruments in Taiwan, and the company
    feeds this registry is built from do not contain it -- ETFs are funds, not
    companies -- so its alerts were refused and dropped."""
    _post(client, _alert(symbol="0050", id="y4"))

    assert db_session.query(Order).one().symbol == "0050.TW"


def test_the_template_now_asks_for_the_exchange(auth_client):
    body = auth_client.get("/api/webhooks/tradingview/setup").json()

    assert "{{exchange}}" in body["example_message"]


def test_the_panel_tells_existing_alerts_to_be_updated(auth_client):
    """{{exchange}} only arrives if the owner puts it in the message, so every
    alert made before today keeps sending nothing and keeps using the weaker
    path. Saying so is the difference between a fix and a fix nobody applied."""
    notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "已經設定好" in notes or "既有" in notes or "舊的" in notes, notes
