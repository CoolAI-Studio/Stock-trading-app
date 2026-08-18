"""Prompt construction and reply clean-up for POST /api/strategies/generate.

Deliberately free of I/O: the AI round trips and the compile+tick validation
both stay in the router, which already owns _validate. What lives here is the
part worth testing on its own -- teaching the model the exact contract, and
digging the code (or the question) back out of whatever the model wrapped it
in.
"""

import dataclasses
import re

from app.services.indicators import INDICATOR_CATEGORIES, catalogue, get_indicator
from app.services.market_data.base import DEFAULT_TIMEFRAME, Bar, Timeframe
from app.services.strategy_runtime import allowed_modules, forbidden_names

# The sandbox rules, the candle sizes, the bar attributes and the indicator
# list are all interpolated, never spelled out: see
# strategy_runtime.allowed_modules() and indicators/registry.py for why a
# retyped copy is a bug in waiting.
_CONTRACT = """You write Python trading strategies for a single-user trading dashboard.

Output ONLY the Python source code. No explanation, no markdown fences. The one
exception is the clarifying question described under ASK, DO NOT GUESS below.

The code must define exactly this shape:

class Strategy:
    def __init__(self):
        self.name = "SHORT_IDENTIFIER"
        self.symbol = "TICKER"
        # whatever state the strategy needs to remember

    # exactly ONE of on_tick / on_bar -- see ENTRY POINTS
    def on_tick(self, current_price: float) -> str:
        ...
        return "HOLD"

ENTRY POINTS

Define exactly one of these two. Defining both is rejected when the strategy is
saved, so choose the one the request is actually phrased in.

  def on_tick(self, current_price: float) -> str
      Called once per price tick -- roughly every 5 seconds -- and given ONE
      price, never a history. A tick is NOT a candle: it has no open, high, low
      or close, and nothing about it can express 收盤 or 第幾根K線. Right for
      "when the price goes above X".

  def on_bar(self, bar) -> str
      Called once per CLOSED candle of self.timeframe: never twice for the same
      candle, and never for the candle that is still forming. Right -- and the
      only correct choice -- for anything phrased in candles.

Both return exactly one of the strings "BUY", "SELL" or "HOLD", and nothing
else. Return "HOLD" while there is not yet enough history to decide.

CANDLES (on_bar only)

self.timeframe, assigned in __init__, chooses which candles arrive. It is a
plain string literal and defaults to "{default_timeframe}" when the strategy
does not say. Which word the user says for which value is listed under READING
THE USER'S WORDS; the values themselves are: {timeframe_values}

The bar handed to on_bar has exactly these attributes and no others:
{bar_reference}

A bar carries no history of its own. Accumulate what you need on `self` --
appending bar.close to a list inside on_bar -- exactly as an on_tick strategy
accumulates prices.

self.warmup_bars = N, also in __init__, is how many closed candles must exist
before this strategy is allowed to signal at all. Set it comfortably above the
longest indicator period used: indicators.macd on its 12/26/9 defaults returns
None for the signal line until the 34th candle, and a decision made on a
warm-up value is a wrong decision that looks like a right one. At start-up the
candles already in history are replayed through on_bar with every signal
discarded, so `self` is warm before the first live candle closes.

HARD RULES
- Anything the strategy needs across calls -- a rolling price window, whether
  it currently holds a position, the entry price, how many candles ago
  something happened -- lives on `self`: initialised in __init__, updated
  inside on_tick/on_bar. There is no other persistence, and the instance is
  reused for every tick and every candle.
- self.name and self.symbol must be plain string literals assigned in
  __init__; they are read back to label the strategy in the UI.
- The code runs in a restricted sandbox: no network access, no filesystem
  access.
- A technical-indicator library is already there, as the global name
  `indicators`. It needs no import and CANNOT be imported. USE IT, and DO NOT
  RE-IMPLEMENT anything in the list below: a hand-written RSI, MACD or ATR is
  very easily subtly wrong -- Wilder smoothing mistaken for a plain EMA, the
  wrong seed value, a signal line off by one -- and wrong code that returns
  plausible numbers is the worst outcome here, because it gets traded on.
- Every indicator takes plain lists of floats that YOU accumulate on `self`,
  oldest candle first, and returns a list of the SAME length with `None` in
  every position that has not warmed up yet. So
  `indicators.rsi(self.closes, 14)[-1]` is the RSI now and `[-2]` is the
  previous one -- always check for `None` before comparing.
- Trim those lists to the longest window the strategy actually needs (a few
  times the largest period is plenty). Every indicator call is linear in the
  list it is given, and the strategy is re-run over hundreds of candles when
  it starts up, so an unbounded list makes start-up slower and slower for no
  gain.
- The ONLY importable modules are: {modules}. Importing anything else -- for
  example {unavailable} -- makes the strategy impossible to save.
- These names are rejected outright and must not appear anywhere in the code:
  {forbidden}. Neither may attribute access to any dunder (__class__,
  __dict__, and so on).
- The user reads Traditional Chinese, so write any comments in Traditional
  Chinese.

READING THE USER'S WORDS

The user is not a programmer and describes strategies the way a Taiwanese
retail trader talks. Translate, do not transliterate:
{vocabulary_reference}

ASK, DO NOT GUESS

If part of the request has more than one reasonable reading, and the readings
would make the strategy behave differently, do NOT pick one. Reply with a
question INSTEAD of a strategy, in exactly this shape:

QUESTION: <the question in Traditional Chinese, spelling out the readings you
are choosing between, e.g. （A）… （B）…>

That block is the whole reply: no code, not even a draft, and no apology. Code
sent alongside a question is discarded. Ask about everything you need in that
ONE question. If the request quotes an earlier question of yours together with
the user's answer, that point is settled -- use their answer, and do not ask
about it again.

The owner cannot read Python. To them a guess is indistinguishable from an
answer, and the answer gets traded on. Asking costs one message; guessing costs
money. Words that almost always need asking:
- 收斂 / 發散 / 沒收斂: is it about the GAP between the two lines still
  widening (abs(fast - slow) growing), or only that they have not crossed back
  over each other? Those are two different strategies.
- 背離: over how many candles, and measured against which highs or lows?
- a request phrased in candles that never says which candle size
- an entry rule with no exit rule, or an exit rule with no entry rule
- 觸發 when it is not clear whether the signal fires once or on every later
  candle that still matches

Do NOT ask about anything that only changes wording: what to call the strategy,
how many candles to keep in a list, formatting.

Available indicators ({indicator_count}), called as `indicators.<name>(...)`:
{indicator_reference}"""

# The words a Taiwanese retail trader actually types, mapped onto this runtime.
# Hand-written, because the mapping is knowledge about language rather than
# about the code -- but every indicator it names is checked against the
# registry in tests, which is where a rename would otherwise leave the model
# calling something the namespace refuses.
_VOCABULARY = """K線週期（使用者說的話 -> self.timeframe 要寫的值）：
{timeframe_reference}
K線 / K棒 / 蠟燭 / 一根K -> one `bar` in on_bar
收盤 / 收盤時 / 收盤價 / K線收盤 / 這根K棒結束 / 收在… -> on_bar, and bar.close
開盤價 -> bar.open;  最高價 -> bar.high;  最低價 -> bar.low
第N根K線 / 第二根K線 / N根之後 / 隔N根 -> on_bar, counting CLOSED candles: when
    the event happens store a counter at 0, add 1 at the end of every LATER
    on_bar, and act on the call where it reaches N. Do not count the candle the
    event happened on. NEVER count ticks, and never use a time delta.
即時 / 盤中 / 現價 / 一到就… / 每秒 -> on_tick
MACD -> indicators.macd(closes), whose three series are named:
    快線 / DIF / DIFF -> macd["{macd_fast}"]
    慢線 / 訊號線 / MACD線 / DEA -> macd["{macd_signal}"]
    柱狀圖 / 紅綠柱 / 柱體 / OSC -> macd["{macd_histogram}"]
黃金交叉 / 金叉 / 交叉向上 / 向上穿越 / 站上 -> compare the two most recent
    CLOSED candles: fast[-2] <= slow[-2] and fast[-1] > slow[-1]
死亡交叉 / 死叉 / 交叉向下 / 向下穿越 / 跌破 -> fast[-2] >= slow[-2] and
    fast[-1] < slow[-1]  (the same two series, the other way round)
超買 -> indicators.rsi above the number the user gave, or 70 if they gave none
超賣 -> indicators.rsi below the number the user gave, or 30 if they gave none
均線 / MA / N日均線 / N週均線 -> indicators.sma over the candle closes
指數均線 / EMA -> indicators.ema;  加權均線 / WMA -> indicators.wma
布林通道 / 布林帶 / 上下軌 -> indicators.bollinger_bands
KD / 隨機指標 -> indicators.stochastic;  威廉指標 -> indicators.williams_r
乖離 / 乖離率 -> how far the price sits from its own moving average
波動 / 真實區間 -> indicators.atr
量 / 成交量 / 爆量 / 量縮 -> bar.volume, and indicators.obv for its running sum
跳空 -> this bar.open against the previous bar's close
長紅 / 長黑 / 實體 -> bar.close against bar.open
上影線 / 下影線 -> bar.high / bar.low against the higher / lower of open, close
買進 / 做多 / 進場 / 買訊 -> return "BUY"
賣出 / 出場 / 停利 / 停損 / 賣訊 / 警訊 / 提醒 -> return "SELL"
觀望 / 不動 / 續抱 / 沒訊號 -> return "HOLD"
警訊 and 提醒 do NOT mean the strategy should send anything: returning the
    signal IS the alert. The dashboard decides whether to notify or to order."""

# Traditional Chinese for each candle size, keyed by the value the runtime
# fetches with -- spelled via the enum member so a changed value moves the term
# with it. Every Timeframe must appear here (tests enforce it): one that does
# not is a candle size the owner can ask for and never get.
_TIMEFRAME_TERMS: dict[str, tuple[str, ...]] = {
    Timeframe.MINUTE_1.value: ("1分線", "分K"),
    Timeframe.MINUTE_5.value: ("5分線", "5分K"),
    Timeframe.MINUTE_15.value: ("15分線", "15分K"),
    Timeframe.HOUR_1.value: ("小時線", "60分線", "時K"),
    Timeframe.DAY_1.value: ("日線", "日K"),
    Timeframe.WEEK_1.value: ("週線", "周線", "週K"),
    Timeframe.MONTH_1.value: ("月線", "月K"),
}

# Same idea for the candle's own fields: the names are read off the dataclass,
# these are what the owner calls them.
_BAR_FIELD_TERMS: dict[str, str] = {
    "symbol": "股票代號",
    "timeframe": "這根K線的週期",
    "timestamp": "這根K線的開始時間（UTC，不是收盤時間）",
    "open": "開盤價",
    "high": "最高價",
    "low": "最低價",
    "close": "收盤價",
    "volume": "成交量",
}

# Named rather than left to "anything else" because these are what a model
# reaches for unprompted. Filtered against the live allowlist before it goes
# into the prompt: widening the sandbox to include one of them later must not
# leave the same prompt still forbidding it.
_COMMON_TEMPTATIONS = (
    "numpy", "pandas", "requests", "httpx", "yfinance", "talib", "os", "sys", "time",
)  # fmt: skip

# Closed fence first: `.*?` stops at the closing ```, whereas the open-fence
# pattern below would swallow it and everything after. An unterminated fence
# means the answer was cut off mid-block; the partial code still goes to the
# validator, whose error says more than "the AI failed" would.
_CLOSED_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"```[^\n]*\n(.*)", re.DOTALL)

# A statement at column 0 -- where the contract's `class Strategy` has to
# begin. This is what tells code apart from chat about code.
_CODE_START_RE = re.compile(r"^(?:class|def|import|from)\b", re.MULTILINE)

_QUESTION_MARKER = "QUESTION:"
# Markdown decoration a model wraps the marker in when it ignores "no
# markdown". `#` is deliberately NOT here: `# QUESTION: ...` is a Python
# comment inside a real strategy, and treating that as a request for input
# would throw away code the owner already paid a round trip for.
_QUESTION_DECORATION = "*_> "


def build_timeframe_reference() -> str:
    """Every candle size the runtime can fetch, with the words users say for
    it. Read from the Timeframe enum so a candle size added to the runtime
    reaches the model without anyone remembering to retype it here."""
    lines = []
    for timeframe in Timeframe:
        terms = " / ".join(_TIMEFRAME_TERMS.get(timeframe.value, ()))
        said = terms or f"(no Traditional Chinese term registered for {timeframe.value})"
        lines.append(f'  {said} -> self.timeframe = "{timeframe.value}"')
    return "\n".join(lines)


def build_bar_reference() -> str:
    """The candle's attributes, read off the Bar dataclass.

    A prompt that promises an attribute the object does not have produces code
    that compiles, runs on the first live candle and dies with AttributeError.
    """
    lines = []
    for field in dataclasses.fields(Bar):
        gloss = _BAR_FIELD_TERMS.get(field.name)
        suffix = f"  # {gloss}" if gloss else ""
        lines.append(f"  bar.{field.name}: {_type_name(field.type)}{suffix}")
    return "\n".join(lines)


def build_vocabulary_reference() -> str:
    """The retail-trader phrasebook, with the MACD series named by the keys the
    indicator really returns -- 快線/慢線 is the owner's entire MACD vocabulary,
    and pointing it at the wrong key would be invisible in the finished code."""
    macd = get_indicator("macd")
    fast, signal, histogram = macd.keys
    return _VOCABULARY.format(
        timeframe_reference=build_timeframe_reference(),
        macd_fast=fast,
        macd_signal=signal,
        macd_histogram=histogram,
    )


def build_indicator_reference() -> str:
    """The indicator catalogue, rendered for the system prompt.

    Read from the registry for the same reason allowed_modules() is: a list
    retyped into the prompt goes stale the moment an indicator is added, and
    the model then writes code against one that is not there -- or, far worse,
    quietly hand-rolls one that is.
    """
    lines: list[str] = []
    current: str | None = None
    for spec in catalogue():
        if spec.category.value != current:
            current = spec.category.value
            lines.append(f"[{current}/{INDICATOR_CATEGORIES[spec.category]}]")
        lines.append(f"  {spec.signature()} -> {spec.returns()}  # {spec.title}")
    return "\n".join(lines)


def _type_name(annotation: object) -> str:
    """`float | None` has no __name__, and printing it as "UnionType" would
    hide exactly the part the model has to handle."""
    return getattr(annotation, "__name__", None) or str(annotation)


def build_system_prompt() -> str:
    allowed = allowed_modules()
    return _CONTRACT.format(
        modules=", ".join(allowed),
        unavailable=", ".join(m for m in _COMMON_TEMPTATIONS if m not in allowed),
        forbidden=", ".join(forbidden_names()),
        default_timeframe=DEFAULT_TIMEFRAME.value,
        timeframe_values=", ".join(f'"{timeframe.value}"' for timeframe in Timeframe),
        bar_reference=build_bar_reference(),
        vocabulary_reference=build_vocabulary_reference(),
        indicator_count=len(catalogue()),
        indicator_reference=build_indicator_reference(),
    )


def build_request_prompt(
    description: str,
    symbol: str | None,
    question: str | None = None,
    answer: str | None = None,
) -> str:
    lines = [f"策略需求：\n{description}"]
    if symbol:
        lines.append(f"self.symbol 必須設為：{symbol}")
    else:
        lines.append("使用者沒有指定標的，請依需求描述挑一個合理的代號填進 self.symbol。")

    # ask() is single-turn, so a question the model asked last round only
    # exists here if it is restated. Without the question the answer is
    # unreadable ("（A）" answers nothing on its own); without the original
    # request the model would drift away from what was actually asked for.
    if answer and answer.strip():
        asked = f"你先前問：{question.strip()}\n" if question and question.strip() else ""
        lines.append(
            "使用者已經回答了你的提問，請直接照這個回答處理，不要再問同一件事：\n"
            f"{asked}使用者回答：{answer.strip()}"
        )
    return "\n\n".join(lines)


def build_repair_prompt(request_prompt: str, source_code: str, error: str) -> str:
    """One shot at self-repair. ask() is single-turn, so the original request
    has to be restated here -- without it the model fixes the reported error
    and drifts away from what the owner actually asked for."""
    return (
        "Your previous answer to the request below was rejected by the sandbox "
        "validator. Fix that specific problem and output ONLY the corrected, "
        "complete Python source -- no explanation, no markdown fences.\n\n"
        f"--- request ---\n{request_prompt}\n\n"
        f"--- validator error ---\n{error}\n\n"
        f"--- your previous code ---\n{source_code}"
    )


def extract_question(reply: str | None) -> str:
    """The clarifying question the model was told to send instead of code.

    Scanned across the whole reply, code fences included, and deliberately
    allowed to win over any code in the same message: a model that hedges by
    asking AND attaching its guess must not have the guess handed over as a
    finished strategy. The owner cannot read Python, so they would have no way
    to tell that a reading was chosen for them.

    Erring towards "this is a question" is therefore the safe direction. The
    only cost of a false positive is one more round trip; the cost of a false
    negative is a strategy that quietly does something else.
    """
    if not reply:
        return ""

    lines = reply.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip().lstrip(_QUESTION_DECORATION).strip()
        if not stripped.startswith(_QUESTION_MARKER):
            continue
        parts = [stripped[len(_QUESTION_MARKER) :].strip()]
        # Options usually arrive on the lines underneath; a blank line or a
        # fence is where the model stopped asking and went back to chatting.
        for following in lines[index + 1 :]:
            text = following.strip()
            if not text or text.startswith("```"):
                break
            parts.append(text)
        return "\n".join(part for part in parts if part).strip()
    return ""


def extract_code(reply: str | None) -> str:
    """Models are told to emit bare source and routinely emit a fenced block
    with a sentence of chat on either side anyway -- sometimes two blocks,
    sometimes a greeting and no fence, sometimes an apology and no code.

    Guessing wrong costs more than tidiness: prose handed to the validator
    comes back as a syntax error, which spends the one repair round -- and its
    slice of the daily allowance -- on an answer that never contained a
    strategy, then shows the owner a Python error about a Chinese sentence.
    """
    if not reply:
        return ""

    blocks = _CLOSED_FENCE_RE.findall(reply)
    if blocks:
        # Two blocks means the model illustrated the rule before writing the
        # strategy, so the first block is the one to skip, not the one to take.
        return next((b for b in blocks if _CODE_START_RE.search(b)), blocks[0]).strip()

    open_fence = _OPEN_FENCE_RE.search(reply)
    if open_fence:
        return open_fence.group(1).strip()

    start = _CODE_START_RE.search(reply)
    return reply[start.start() :].strip() if start else ""
