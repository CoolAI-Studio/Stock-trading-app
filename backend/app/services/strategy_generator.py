"""Prompt construction and reply clean-up for POST /api/strategies/generate.

Deliberately free of I/O: the AI round trips and the compile+tick validation
both stay in the router, which already owns _validate. What lives here is the
part worth testing on its own -- teaching the model the exact contract, and
digging the code back out of whatever the model wrapped it in.
"""

import re

from app.services.strategy_runtime import allowed_modules, forbidden_names

# The sandbox rules are interpolated, never spelled out: see
# strategy_runtime.allowed_modules() for why a retyped copy is a bug in waiting.
_CONTRACT = """You write Python trading strategies for a single-user trading dashboard.

Output ONLY the Python source code. No explanation, no markdown fences.

The code must define exactly this shape:

class Strategy:
    def __init__(self):
        self.name = "SHORT_IDENTIFIER"
        self.symbol = "TICKER"
        # whatever state the strategy needs to remember

    def on_tick(self, current_price: float) -> str:
        ...
        return "HOLD"

Hard rules:
- on_tick() is called once per price tick and is given ONE price, never a
  history. It must return exactly one of the strings "BUY", "SELL" or "HOLD",
  and nothing else.
- Anything the strategy needs across ticks -- a rolling price window, whether
  it currently holds a position, the entry price -- lives on `self`:
  initialised in __init__, updated inside on_tick. There is no other
  persistence, and the instance is reused for every tick.
- Return "HOLD" while there is not yet enough history to decide.
- self.name and self.symbol must be plain string literals assigned in
  __init__; they are read back to label the strategy in the UI.
- The code runs in a restricted sandbox: no network access, no filesystem
  access. Compute every indicator from the prices you accumulated yourself.
- The ONLY importable modules are: {modules}. Importing anything else --
  numpy, pandas, requests, httpx, yfinance, talib, os, sys, time -- makes the
  strategy impossible to save.
- These names are rejected outright and must not appear anywhere in the code:
  {forbidden}. Neither may attribute access to any dunder (__class__,
  __dict__, and so on).
- The user reads Traditional Chinese, so write any comments in Traditional
  Chinese."""

# Closed fence first: `.*?` stops at the closing ```, whereas the open-fence
# pattern below would swallow it and everything after. An unterminated fence
# means the answer was cut off mid-block; the partial code still goes to the
# validator, whose error says more than "the AI failed" would.
_CLOSED_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"```[^\n]*\n(.*)", re.DOTALL)


def build_system_prompt() -> str:
    return _CONTRACT.format(
        modules=", ".join(allowed_modules()),
        forbidden=", ".join(forbidden_names()),
    )


def build_request_prompt(description: str, symbol: str | None) -> str:
    lines = [f"策略需求：\n{description}"]
    if symbol:
        lines.append(f"self.symbol 必須設為：{symbol}")
    else:
        lines.append("使用者沒有指定標的，請依需求描述挑一個合理的代號填進 self.symbol。")
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


def extract_code(reply: str | None) -> str:
    """Models are told to emit bare source and routinely emit a fenced block
    with a sentence of chat on either side anyway."""
    if not reply:
        return ""
    match = _CLOSED_FENCE_RE.search(reply) or _OPEN_FENCE_RE.search(reply)
    return (match.group(1) if match else reply).strip()
