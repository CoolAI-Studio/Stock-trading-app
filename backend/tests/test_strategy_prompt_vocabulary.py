"""What the generation prompt teaches, and that it is derived rather than retyped.

Two separate jobs in here. The first is the one test_strategy_generate_api.py
already does for the sandbox allowlist: prove the candle sizes, the bar
attributes and the indicator names in the prompt are read off the runtime, so
widening the runtime widens the prompt. A hardcoded copy rots silently and the
model starts writing calls to things that are not there.

The second is the vocabulary. The owner writes 「週線」、「快慢線」、「第二根
K線」, not `self.timeframe` or `macd["signal"]`, and a prompt that does not
carry that mapping gets code which reads right and means something else.
"""

import dataclasses
import re
from enum import StrEnum

import pytest

from app.services import strategy_generator
from app.services.indicators import catalogue, get_indicator
from app.services.market_data.base import Bar, Timeframe
from app.services.strategy_generator import (
    build_bar_reference,
    build_request_prompt,
    build_system_prompt,
    build_timeframe_reference,
    build_vocabulary_reference,
    extract_question,
)

# Anything the prose spells as a call on the injected namespace. Used to hold
# the hand-written half of the prompt to the registry.
_INDICATOR_CALL_RE = re.compile(r"indicators\.([a-z_0-9]+)")


# --- derived, not retyped ---------------------------------------------------


@pytest.mark.parametrize("timeframe", list(Timeframe), ids=lambda tf: tf.value)
def test_every_candle_size_reaches_the_prompt_with_the_words_users_say(timeframe):
    """A timeframe the runtime can fetch but the prompt never names is one the
    owner can ask for and never get."""
    assert timeframe.value in strategy_generator._TIMEFRAME_TERMS, (
        f"{timeframe.value} has no Traditional Chinese term, so the model cannot "
        "recognise the owner asking for it"
    )

    reference = build_timeframe_reference()
    line = next((row for row in reference.splitlines() if f'"{timeframe.value}"' in row), None)
    assert line is not None, f"{timeframe.value} is missing from the prompt"
    for term in strategy_generator._TIMEFRAME_TERMS[timeframe.value]:
        # Same line, not merely somewhere in the prompt: the mapping is the
        # point, and two facts a paragraph apart are not a mapping.
        assert term in line, f"{term} is not on the same line as {timeframe.value}"


def test_the_candle_sizes_follow_the_runtime_enum(monkeypatch):
    """The point of deriving them: a timeframe added to the runtime has to
    show up in the prompt without anyone remembering to retype it."""

    class FakeTimeframe(StrEnum):
        DAY_1 = "1d"
        QUARTER_1 = "3mo"

    monkeypatch.setattr(strategy_generator, "Timeframe", FakeTimeframe)

    reference = build_timeframe_reference()

    assert '"3mo"' in reference
    assert '"1wk"' not in reference


@pytest.mark.parametrize("field", dataclasses.fields(Bar), ids=lambda f: f.name)
def test_every_bar_attribute_is_described_to_the_model(field):
    reference = build_bar_reference()
    assert f"bar.{field.name}" in reference
    assert field.name in strategy_generator._BAR_FIELD_TERMS, (
        f"bar.{field.name} has no Traditional Chinese gloss"
    )


def test_the_bar_attributes_follow_the_dataclass(monkeypatch):
    @dataclasses.dataclass(frozen=True)
    class FakeBar:
        close: float
        turnover: float | None = None

    monkeypatch.setattr(strategy_generator, "Bar", FakeBar)

    reference = build_bar_reference()

    assert "bar.turnover: float | None" in reference
    assert "bar.high" not in reference


def test_the_fast_and_slow_line_vocabulary_names_the_real_macd_keys():
    """快線/慢線 is the owner's whole MACD vocabulary, and it has to land on
    the keys the indicator actually returns -- in the registry's order, so a
    reordered or renamed key fails here instead of teaching the model to read
    the histogram as the signal line."""
    spec = get_indicator("macd")
    assert spec.keys[:2] == ("macd", "signal")

    vocabulary = build_vocabulary_reference()
    fast = next(row for row in vocabulary.splitlines() if "快線" in row)
    slow = next(row for row in vocabulary.splitlines() if "慢線" in row)

    assert f'["{spec.keys[0]}"]' in fast
    assert f'["{spec.keys[1]}"]' in slow


def test_every_indicator_the_prose_names_actually_exists():
    """The catalogue is generated, but the vocabulary section names indicators
    by hand -- that is where a rename would leave the model calling something
    the namespace will refuse."""
    registered = {spec.name for spec in catalogue()}
    mentioned = set(_INDICATOR_CALL_RE.findall(build_system_prompt()))

    assert mentioned, "the prompt no longer names a single indicator by name"
    assert mentioned <= registered, f"not in the registry: {sorted(mentioned - registered)}"


# --- the words a non-programmer actually writes ------------------------------


@pytest.mark.parametrize(
    ("term", "must_teach"),
    [
        ("週線", '"1wk"'),
        ("日線", '"1d"'),
        ("月線", '"1mo"'),
        ("收盤", "on_bar"),
        ("第N根K線", "on_bar"),
        ("現價", "on_tick"),
        ("黃金交叉", "[-1]"),
        ("死亡交叉", "[-1]"),
        ("超買", "indicators.rsi"),
        ("超賣", "indicators.rsi"),
        ("均線", "indicators.sma"),
        ("布林", "indicators.bollinger_bands"),
        ("成交量", "bar.volume"),
        ("停損", '"SELL"'),
        ("警訊", '"SELL"'),
    ],
)
def test_the_prompt_translates_the_words_a_non_programmer_uses(term, must_teach):
    vocabulary = build_vocabulary_reference()
    assert term in vocabulary, f"the prompt never mentions {term}"
    assert must_teach in vocabulary


def test_the_prompt_spells_out_how_to_count_the_nth_candle_after_an_event():
    """「第二根K線」 is the owner's own wording, and counting ticks instead of
    closed candles is exactly the silent wrong answer this all exists to
    prevent."""
    vocabulary = build_vocabulary_reference()

    assert "第N根K線" in vocabulary
    assert "never count ticks" in vocabulary.lower()


# --- ask rather than guess ---------------------------------------------------


def test_the_prompt_tells_the_model_to_ask_instead_of_guessing():
    prompt = build_system_prompt()

    assert "QUESTION:" in prompt
    # 收斂 is the ambiguity in the owner's own sentence; if the prompt does not
    # name it, the model settles it by guessing and nobody ever finds out.
    assert "收斂" in prompt
    assert "no code, not even a draft" in prompt.lower()


def test_a_question_reply_is_recognised_and_returned_as_text():
    reply = "QUESTION: 「快慢線沒收斂」是指兩線距離還在擴大，還是只要沒有再交叉回來就算？"

    assert extract_question(reply) == (
        "「快慢線沒收斂」是指兩線距離還在擴大，還是只要沒有再交叉回來就算？"
    )


def test_a_question_keeps_the_options_listed_under_it():
    reply = "QUESTION: 「沒收斂」要怎麼判斷？\n（A）兩線距離持續擴大\n（B）只要沒交叉回來\n\n謝謝！"

    question = extract_question(reply)

    assert "（A）兩線距離持續擴大" in question
    assert "（B）只要沒交叉回來" in question
    assert "謝謝" not in question


def test_ordinary_code_is_not_mistaken_for_a_question():
    source = 'class Strategy:\n    def __init__(self):\n        self.name = "X"\n'

    assert extract_question(source) == ""
    assert extract_question("") == ""
    assert extract_question(None) == ""


def test_a_question_wins_over_code_sent_alongside_it():
    """The whole point of asking. A model that hedges by asking AND attaching
    its guess must not have the guess handed to the owner as a finished
    strategy -- they cannot read Python, so they would never know it guessed."""
    reply = (
        "QUESTION: 「沒收斂」是指距離還在擴大嗎？\n\n"
        "```python\nclass Strategy:\n    def __init__(self):\n        self.name = 'guess'\n```\n"
    )

    assert extract_question(reply).startswith("「沒收斂」")


def test_a_python_comment_is_not_a_question():
    """`# QUESTION:` is a comment inside real code, not a request for input --
    swallowing the strategy over it would cost the owner the answer they paid
    a daily-quota round trip for."""
    source = "class Strategy:\n    # QUESTION: 這裡要不要加停損？\n    pass\n"

    assert extract_question(source) == ""


def test_an_answered_question_is_carried_into_the_next_request():
    prompt = build_request_prompt(
        "台積電周線 RSI>80 後賣出",
        "2330.TW",
        question="「沒收斂」是指距離還在擴大嗎？",
        answer="對，指兩線距離還在擴大",
    )

    assert "「沒收斂」是指距離還在擴大嗎？" in prompt
    assert "對，指兩線距離還在擴大" in prompt
    assert "不要再問同一件事" in prompt


def test_an_unanswered_request_carries_no_clarification_section():
    prompt = build_request_prompt("台積電周線 RSI>80 後賣出", "2330.TW")

    assert "不要再問同一件事" not in prompt
