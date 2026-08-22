"""在一支美股上回測 12 小時線，會安靜地跑出一個空結果。

`market.py` 的兩個端點、以及 `strategies.py` 的建立與修改，都已經有「這個資料
來源有沒有這個週期」的閘門。`backtests.py` 沒有——`_resolve_timeframe` 根本收不到
`data_source`。

WHY IT MATTERS MORE HERE THAN IT LOOKS. Yahoo 對一個不支援的 interval 回的是
**空的 frame，不是錯誤**。所以這個請求不會壞，它會成功地測完零根 K 棒，然後回一
份「沒有交易」的報告——而那份報告讀起來跟「這個策略在這段期間不會進場」一模一樣。
一個看起來完成、實際上什麼都沒測的結果，是這裡最糟的輸出。

同一個 codebase 裡已經有正確的寫法（`market._refuse_unsupported`），這一條要的是
讓回測也走同一道閘。
"""

from datetime import UTC, datetime

ON_TICK = """
class Strategy:
    def __init__(self):
        self.name = "ticker"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""


def _payload(**overrides) -> dict:
    body = {
        "source_code": ON_TICK,
        "symbol": "AAPL",
        "data_source": "yfinance",
        "start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
    }
    body.update(overrides)
    return body


def test_a_candle_that_source_does_not_serve_is_refused(auth_client):
    response = auth_client.post("/api/backtests", json=_payload(timeframe="12h"))

    assert response.status_code == 422


def test_and_the_refusal_says_what_can_be_chosen_instead(auth_client):
    """「不支援」不是訊息。他要知道可以選什麼——而這個 app 別的地方已經是這樣講的。"""
    response = auth_client.post("/api/backtests", json=_payload(timeframe="12h"))

    detail = response.json()["detail"]
    assert "可以選" in str(detail)
    assert "日" in str(detail)


def test_a_candle_the_source_does_serve_still_goes_through(auth_client):
    """把閘門做過頭就等於把回測關掉。"""
    response = auth_client.post("/api/backtests", json=_payload(timeframe="1d"))

    assert response.status_code != 422


def test_crypto_keeps_the_candle_only_it_has(auth_client):
    """12 小時線在 Binance 上是有的。這道閘門是「這個來源」的問題，不是週期本身
    的問題。"""
    response = auth_client.post(
        "/api/backtests",
        json=_payload(symbol="BTCUSDT", data_source="binance", timeframe="12h"),
    )

    assert response.status_code != 422
