"""Where a risk knob's *effective* value is decided.

RiskSettings stays global -- one row per user, covering everything. A Strategy
may override any of OVERRIDABLE_FIELDS by holding a non-NULL value of its own;
NULL means inherit, which is what every strategy written before this module
existed holds.

The coalescing lives here and nowhere else on purpose. Spread across the four
gates that consume it, one of them would keep reading the global value forever
the day a field was added and that site was missed -- and a risk gate quietly
using the wrong number looks exactly like a risk gate that works.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.risk import RiskSettings
from app.models.strategy import Strategy

# Every knob a strategy may override. The Strategy model carries a nullable
# column of the same name for each, and the API schemas iterate this list --
# so a knob added in one place and forgotten in another shows up as a failing
# test rather than as a setting that silently does nothing.
OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "capital",
    "stop_loss_pct",
    "take_profit_pct",
    "max_position_qty",
    "max_order_notional",
    "max_pending_orders_per_symbol",
    "signal_cooldown_sec",
    "alert_interval_sec",
)


@dataclass(frozen=True)
class EffectiveRiskSettings:
    """The numbers a gate should actually use, global and per-strategy
    already reconciled."""

    capital: Decimal
    stop_loss_pct: Decimal
    take_profit_pct: Decimal
    max_position_qty: Decimal
    max_order_notional: Decimal
    max_pending_orders_per_symbol: int
    signal_cooldown_sec: int
    alert_interval_sec: int

    # Which strategy's overrides were applied, or None when the values are
    # purely global. Carried so a caller can say whose settings a decision was
    # made under instead of leaving the owner to guess.
    strategy_id: int | None = None


def get_or_create_global(db: Session, user_id: int) -> RiskSettings:
    """Flushed, not committed: the caller owns the transaction, and every one
    of them commits (or rolls back) shortly after."""
    row = db.query(RiskSettings).filter(RiskSettings.user_id == user_id).first()
    if row is None:
        row = RiskSettings(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def resolve(
    global_settings: RiskSettings, strategy: Strategy | None = None
) -> EffectiveRiskSettings:
    """The one place NULL-means-inherit is decided."""
    values = {}
    for field in OVERRIDABLE_FIELDS:
        override = None if strategy is None else getattr(strategy, field)
        # Three states, and they must stay three: NULL inherits, 0 means the
        # knob is switched off for this strategy (see services/risk.py), any
        # other number is that limit. Hence `is None` and never a truthiness
        # test -- `override or global` would read a deliberate 0 as "unset"
        # and hand the strategy back the very global stop-loss it turned off.
        values[field] = getattr(global_settings, field) if override is None else override
    return EffectiveRiskSettings(
        **values, strategy_id=None if strategy is None else strategy.id
    )


def resolve_for_user(
    db: Session, user_id: int, strategy: Strategy | None = None
) -> EffectiveRiskSettings:
    """resolve() for callers that hold a user rather than a settings row."""
    return resolve(get_or_create_global(db, user_id), strategy)
