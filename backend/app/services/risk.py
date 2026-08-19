from decimal import Decimal

"""Pure risk-gating functions, ported from the legacy src/risk_control.py
(RiskControl.check_stop_loss / check_take_profit / check_position_limit).
Stateless by design -- position state now lives in the `positions` table,
not an in-memory counter.

ONE RULE FOR ZERO, everywhere: a risk knob at <= 0 is switched OFF -- no
limit, no trigger. It holds for all eight knobs: the four gated here, the
three gated in services/signals.py (max_order_notional, signal_cooldown_sec,
max_pending_orders_per_symbol), and alert_interval_sec in services/alerts.py.
So the settings UI can offer one "不限制" switch, not eight special cases.
Please do not reintroduce a per-field exception: three of these fields used
to read 0 literally, which made stop_loss_pct = 0 sell the instant price
touched cost and max_pending_orders_per_symbol = 0 block every order
silently. Both looked like malfunctions, not like settings.
"""


def check_stop_loss(entry_price: Decimal, current_price: Decimal, stop_loss_pct: Decimal) -> bool:
    """True = the position has fallen far enough to cut.

    `stop_loss_pct <= 0` means no stop-loss at all. Read literally, 0 makes
    the threshold the entry price itself and every quote at or below cost a
    sell -- the loudest possible reading of what the owner typed to mean
    "don't do this".
    """
    if stop_loss_pct <= 0:
        return False
    return current_price <= entry_price * (Decimal(1) - stop_loss_pct)


def check_take_profit(
    entry_price: Decimal, current_price: Decimal, take_profit_pct: Decimal
) -> bool:
    """True = the position has gained enough to take the profit.

    `take_profit_pct <= 0` means no take-profit, the same convention
    check_stop_loss uses. Read literally, 0 sells on any gain at all --
    including a price that has merely come back to cost.
    """
    if take_profit_pct <= 0:
        return False
    if entry_price <= 0:
        return False
    return (current_price - entry_price) / entry_price >= take_profit_pct


def check_position_limit(
    current_position: Decimal, incoming_qty: Decimal, max_position: Decimal
) -> bool:
    """True = the incoming quantity is allowed.

    Ported verbatim: hitting the cap exactly is treated as exceeding it
    (strict `<`, not `<=`) -- per the legacy code's own comment, "equal to
    the limit also counts as exceeding it".

    `max_position <= 0` is treated as "not configured yet" (allow), since
    a brand-new user's risk settings default to 0 and that must not mean
    "block every order forever" until they visit the risk settings page.
    """
    if max_position <= 0:
        return True
    return (current_position + incoming_qty) < max_position


def check_capital_limit(committed_cost: Decimal, incoming_cost: Decimal, capital: Decimal) -> bool:
    """True = the incoming buy fits inside the allocated capital.

    `capital <= 0` means "not configured yet" (allow), the same convention
    check_position_limit uses above -- and here it is load-bearing rather than
    merely tidy. capital has been stored and displayed since v1 but enforced
    nowhere, so every row in the wild holds 0. Reading that literally the day
    this gate ships would reject every buy the owner makes.

    Unlike check_position_limit this is `<=`, not `<`: that one inherited a
    strict comparison from the legacy RiskControl, whereas spending exactly
    the capital you allocated is the allocation being used, not exceeded.
    """
    if capital <= 0:
        return True
    return (committed_cost + incoming_cost) <= capital
