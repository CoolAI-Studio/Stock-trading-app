"""The symbol the AI picks has to be one this app can actually price.

`build_request_prompt` told the model, when the owner had not named a symbol:

    「使用者沒有指定標的，請依需求描述挑一個合理的代號填進 self.symbol。」

and nothing anywhere said what a valid symbol looks like. So for a request
written in Chinese -- 「幫我寫一個台積電的均線策略」 -- 「合理的代號」 is 「台積電」
or 「2330」, both of which are exactly the two shapes the rest of this app spent
weeks learning to refuse:

  「台積電」 stores a strategy that runs forever and never sees a price.
  「2330」 is worse -- Yahoo resolves a bare 2330 to an unrelated Japanese
  company, so it PRICES, and the strategy trades signals off the wrong stock.

Two changes, because the model can still get it wrong:

  THE PROMPT SAYS THE FORMAT. Cheap, and it removes most of the problem.
  THE VALIDATOR SAYS SO OUT LOUD. The editor printed 「偵測到：均線（2330）」 in
  green, which reads as approval. The refusal did eventually happen -- at save
  time, from a different field, with nothing connecting it to the symbol the AI
  chose.
"""

from app.services.strategy_generator import build_request_prompt

MA_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "ma"
        self.symbol = "2330"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""

# --- the prompt -------------------------------------------------------------


def test_the_prompt_says_what_a_taiwanese_symbol_looks_like():
    prompt = build_request_prompt("台積電的均線策略", symbol=None)

    assert "2330.TW" in prompt
    assert ".TWO" in prompt, "the OTC board takes a different suffix and is half the market"


def test_the_prompt_forbids_the_two_shapes_that_actually_get_written():
    prompt = build_request_prompt("台積電的均線策略", symbol=None)

    assert "中文" in prompt, "「台積電」 is what a Chinese request invites"
    assert "只寫" in prompt or "不可以" in prompt, prompt


def test_the_rule_is_there_even_when_the_owner_named_the_symbol():
    """The model is told 「self.symbol 必須設為 2330.TW」 and can still write
    「2330」 into the literal -- and then the strategy is about a different
    company than the row it is saved on."""
    prompt = build_request_prompt("均線策略", symbol="2330.TW")

    assert "2330.TW" in prompt
    assert "中文" in prompt


def test_the_prompt_covers_the_other_two_markets():
    prompt = build_request_prompt("比特幣策略", symbol=None)

    assert "AAPL" in prompt
    assert "BTCUSDT" in prompt


# --- the validator ----------------------------------------------------------


def test_a_bare_taiwanese_code_is_flagged_where_the_code_is_shown(auth_client):
    """The dangerous one: it does not fail, it succeeds on the wrong company."""
    body = auth_client.post("/api/strategies/validate", json={"source_code": MA_SOURCE}).json()

    assert body["symbol_problem"], body
    assert "2330.TW" in body["symbol_problem"]


def test_the_code_itself_still_counts_as_valid(auth_client):
    """The symbol is wrong; the Python is not. Reporting ok:false would hide
    a strategy the owner only has to change one string in."""
    body = auth_client.post("/api/strategies/validate", json={"source_code": MA_SOURCE}).json()

    assert body["ok"] is True


def test_a_chinese_company_name_is_flagged_too(auth_client):
    source = MA_SOURCE.replace('"2330"', '"台積電"')

    body = auth_client.post("/api/strategies/validate", json={"source_code": source}).json()

    assert body["symbol_problem"], body


def test_a_good_symbol_is_not_flagged(auth_client):
    source = MA_SOURCE.replace('"2330"', '"2330.TW"')

    body = auth_client.post("/api/strategies/validate", json={"source_code": source}).json()

    assert body["symbol_problem"] is None
