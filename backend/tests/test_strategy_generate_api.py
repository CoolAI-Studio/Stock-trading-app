from unittest.mock import patch

from app.services import strategy_runtime
from app.services.ai_provider import AIResult

# .strip() throughout: the endpoint trims the whitespace it peels the code
# out of, so untrimmed fixtures would never compare equal to a response.
GENERATED_SOURCE = '''class Strategy:
    def __init__(self):
        self.name = "TSMC_MA5"
        self.symbol = "2330.TW"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if len(self.prices) < 5:
            return "HOLD"
        ma5 = sum(self.prices[-5:]) / 5
        return "BUY" if current_price > ma5 else "HOLD"
'''.strip()

# The exact failure the sandbox exists to catch, and the one an LLM is most
# likely to produce: reaching for a networking library it saw in training data.
SANDBOX_VIOLATING_SOURCE = '''import requests


class Strategy:
    def __init__(self):
        self.name = "Phones_Home"
        self.symbol = "2330.TW"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
'''.strip()


class FakeProvider:
    """Stands in for get_ai_provider(): the suite must never make a real
    network call. Each queued reply is one AI round trip, so a test that
    allows two and gets three fails loudly on the empty queue."""

    def __init__(self, *replies: AIResult) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, str | None]] = []

    def ask(self, message: str, system: str | None = None) -> AIResult:
        self.calls.append({"message": message, "system": system})
        return self._replies.pop(0)


def _generate(auth_client, provider, **payload):
    body = {"description": "用五日均線做多台積電", **payload}
    with patch("app.api.routers.strategies.get_ai_provider", return_value=provider):
        return auth_client.post("/api/strategies/generate", json=body)


def test_generate_returns_code_and_the_same_fields_as_validate(auth_client):
    provider = FakeProvider(AIResult(ok=True, reply=GENERATED_SOURCE))

    resp = _generate(auth_client, provider, symbol="2330.TW")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert body["source_code"] == GENERATED_SOURCE
    assert body["detected_name"] == "TSMC_MA5"
    assert body["detected_symbol"] == "2330.TW"
    assert body["sample_signals"]
    assert len(provider.calls) == 1


def test_generate_strips_markdown_fences_and_surrounding_prose(auth_client):
    reply = f"好的，這是你要的策略：\n\n```python\n{GENERATED_SOURCE}```\n\n希望有幫助！"
    provider = FakeProvider(AIResult(ok=True, reply=reply))

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is True, body
    assert "```" not in body["source_code"]
    assert "希望有幫助" not in body["source_code"]
    assert body["source_code"].startswith("class Strategy:")


def test_code_that_violates_the_sandbox_is_repaired_on_a_second_attempt(auth_client):
    provider = FakeProvider(
        AIResult(ok=True, reply=SANDBOX_VIOLATING_SOURCE),
        AIResult(ok=True, reply=GENERATED_SOURCE),
    )

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is True, body
    assert body["source_code"] == GENERATED_SOURCE
    assert len(provider.calls) == 2
    # The repair round has to name the actual validator complaint, otherwise
    # the model is just guessing a second time.
    repair_prompt = provider.calls[1]["message"]
    assert "requests" in repair_prompt
    assert SANDBOX_VIOLATING_SOURCE in repair_prompt


def test_a_failed_repair_returns_the_code_and_the_error_instead_of_retrying(auth_client):
    """Free-tier models are rate limited, so exactly one repair attempt is
    spent -- and the owner still gets the code plus an honest error rather
    than silence."""
    provider = FakeProvider(
        AIResult(ok=True, reply=SANDBOX_VIOLATING_SOURCE),
        AIResult(ok=True, reply=SANDBOX_VIOLATING_SOURCE),
    )

    resp = _generate(auth_client, provider)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["source_code"] == SANDBOX_VIOLATING_SOURCE
    assert "requests" in body["error"]
    assert len(provider.calls) == 2


def test_a_rate_limited_repair_round_still_returns_the_first_attempt(auth_client):
    provider = FakeProvider(
        AIResult(ok=True, reply=SANDBOX_VIOLATING_SOURCE),
        AIResult(ok=False, error="AI 服務目前繁忙（HTTP 429）：請稍後再試。"),
    )

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is False
    assert body["source_code"] == SANDBOX_VIOLATING_SOURCE
    assert "429" in body["error"]


def test_generate_reports_an_unconfigured_ai_key(auth_client, monkeypatch):
    """Goes through the real provider (no mock) -- the owner must be told
    which setting is missing, not handed a generic failure."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")

    resp = auth_client.post("/api/strategies/generate", json={"description": "均線策略"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "AI_API_KEY" in body["error"]
    assert body["source_code"] is None


def test_generate_reports_a_reply_with_no_code_in_it(auth_client):
    provider = FakeProvider(AIResult(ok=True, reply="   "))

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is False
    assert body["error"]


def test_system_prompt_carries_the_real_sandbox_allowlist(auth_client):
    provider = FakeProvider(AIResult(ok=True, reply=GENERATED_SOURCE))

    _generate(auth_client, provider)

    system_prompt = provider.calls[0]["system"]
    for module in strategy_runtime._ALLOWED_MODULES:
        assert module in system_prompt, f"{module} missing from the generation prompt"
    assert "BUY" in system_prompt and "SELL" in system_prompt and "HOLD" in system_prompt


def test_system_prompt_follows_the_allowlist_when_the_sandbox_changes(auth_client, monkeypatch):
    """The point of deriving the list: widening the sandbox later must widen
    the prompt too, or the model keeps generating code the sandbox rejects."""
    monkeypatch.setattr(
        strategy_runtime, "_ALLOWED_MODULES", frozenset({"math", "zoneinfo"})
    )
    provider = FakeProvider(AIResult(ok=True, reply=GENERATED_SOURCE))

    _generate(auth_client, provider)

    system_prompt = provider.calls[0]["system"]
    assert "zoneinfo" in system_prompt
    assert "statistics" not in system_prompt


def test_requested_symbol_is_passed_to_the_model(auth_client):
    provider = FakeProvider(AIResult(ok=True, reply=GENERATED_SOURCE))

    _generate(auth_client, provider, symbol="2330.TW")

    assert "2330.TW" in provider.calls[0]["message"]


# A crossover strategy rather than the one-liner above: the end-to-end test
# has to see BUY and SELL actually come out, not just "no exception".
CROSSOVER_SOURCE = '''class Strategy:
    def __init__(self):
        self.name = "MA5_MA20_CROSS"
        self.symbol = "2330.TW"
        self.prices = []
        self.holding = False

    def on_tick(self, current_price: float) -> str:
        # 累積價格，均線要自己算，沙箱裡沒有 pandas
        self.prices.append(current_price)
        if len(self.prices) < 20:
            return "HOLD"
        ma5 = sum(self.prices[-5:]) / 5
        ma20 = sum(self.prices[-20:]) / 20
        if ma5 > ma20 and not self.holding:
            self.holding = True
            return "BUY"
        if ma5 < ma20 and self.holding:
            self.holding = False
            return "SELL"
        return "HOLD"
'''.strip()


def test_an_apology_with_no_code_does_not_spend_the_repair_round(auth_client):
    """A refusal is not something the repair round can fix, so spending a
    round trip on it burns the daily allowance for nothing -- and handing the
    apology to the validator answers the owner with a Python syntax error
    about their own Chinese sentence."""
    provider = FakeProvider(
        AIResult(ok=True, reply="抱歉，我無法根據這個描述產生策略。可以再說得具體一點嗎？")
    )

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is False
    assert body["source_code"] is None
    assert "沒有回傳任何程式碼" in body["error"]
    assert len(provider.calls) == 1


def test_the_strategy_is_picked_out_of_a_reply_with_several_fenced_blocks(auth_client):
    """Models like to illustrate the rule in one block before writing the
    strategy in the next. Taking the first block hands pseudo-code to the
    validator and spends the repair round undoing the mistake."""
    reply = (
        "先說明判斷條件：\n\n```text\nMA5 > MA20 -> 買進\n```\n\n"
        f"完整程式碼如下：\n\n```python\n{GENERATED_SOURCE}\n```\n\n有問題再問我。"
    )
    provider = FakeProvider(AIResult(ok=True, reply=reply))

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is True, body
    assert body["source_code"] == GENERATED_SOURCE
    assert len(provider.calls) == 1


def test_a_lead_in_sentence_before_unfenced_code_is_dropped(auth_client):
    """The contract asks for bare source, and a model that obeys it still
    tends to greet you first."""
    provider = FakeProvider(AIResult(ok=True, reply=f"好的，這是你要的策略：\n{GENERATED_SOURCE}"))

    resp = _generate(auth_client, provider)

    body = resp.json()
    assert body["ok"] is True, body
    assert body["source_code"] == GENERATED_SOURCE
    assert len(provider.calls) == 1


def test_the_prompt_never_names_an_allowed_module_as_unavailable(auth_client, monkeypatch):
    """The counter-examples earn their place -- they are what a model reaches
    for -- but they are hand-written, so they get filtered against the live
    allowlist. Otherwise allowing `time` leaves the prompt still forbidding it
    and the model obeys the contradiction it was given last."""
    monkeypatch.setattr(strategy_runtime, "_ALLOWED_MODULES", frozenset({"math", "time"}))
    provider = FakeProvider(AIResult(ok=True, reply=GENERATED_SOURCE))

    _generate(auth_client, provider)

    system_prompt = provider.calls[0]["system"]
    clause = system_prompt.partition("Importing anything else")[2].partition("makes the")[0]
    assert "pandas" in clause
    assert "time" not in clause


def test_a_realistic_reply_yields_code_that_saves_and_ticks(auth_client):
    """End to end on the shape a real model actually returns -- prose, a
    throwaway block, the real block, more prose. What comes out has to clear
    POST /strategies (the same validation, no shortcut) and answer
    BUY/SELL/HOLD across a full price series, not merely compile."""
    reply = (
        "好的！我幫你寫了一個 5 日與 20 日均線的黃金交叉策略。\n\n"
        "判斷條件：\n\n```text\nMA5 > MA20 且尚未持有 -> 買進\n```\n\n"
        f"完整程式碼：\n\n```python\n{CROSSOVER_SOURCE}\n```\n\n記得先自己讀過再啟用！"
    )
    provider = FakeProvider(AIResult(ok=True, reply=reply))

    resp = _generate(auth_client, provider)
    body = resp.json()
    assert body["ok"] is True, body
    assert body["source_code"] == CROSSOVER_SOURCE
    assert body["detected_name"] == "MA5_MA20_CROSS"
    assert len(provider.calls) == 1

    created = auth_client.post(
        "/api/strategies",
        json={"name": "ma-cross", "symbol": "2330.TW", "source_code": body["source_code"]},
    )
    assert created.status_code == 201, created.text

    loaded = strategy_runtime.compile_strategy(body["source_code"])
    rising = [100.0 + i for i in range(30)]
    falling = [130.0 - 2 * i for i in range(30)]
    signals = [loaded.on_tick(price) for price in rising + falling]

    assert set(signals) <= {"BUY", "SELL", "HOLD"}
    assert "BUY" in signals
    assert "SELL" in signals


def test_generate_requires_auth(client):
    resp = client.post("/api/strategies/generate", json={"description": "均線策略"})
    assert resp.status_code == 401
