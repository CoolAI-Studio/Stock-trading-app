from decimal import Decimal

"""Pure risk-gating functions, ported from the legacy src/risk_control.py
(RiskControl.check_stop_loss / check_take_profit / check_position_limit).
Stateless by design -- position state now lives in the `positions` table,
not an in-memory counter."""


def check_stop_loss(entry_price: Decimal, current_price: Decimal, stop_loss_pct: Decimal) -> bool:
    return current_price <= entry_price * (Decimal(1) - stop_loss_pct)


def check_take_profit(
    entry_price: Decimal, current_price: Decimal, take_profit_pct: Decimal
) -> bool:
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
