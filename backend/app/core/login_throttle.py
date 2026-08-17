import time
from dataclasses import dataclass

# In-memory and process-local, like app/ws/tickets.py -- fine because the app
# already has to run with `--workers 1` (app/services/market_loop.py), so there
# is exactly one process holding this state and no extra dependency (Redis, a
# DB table) is needed for it to be correct.
#
# The trade-off: a restart forgets every counter and lockout. On Render's free
# tier that happens on each deploy and after an idle sleep, so a patient
# attacker could get a fresh set of attempts by waiting for one. That is still
# orders of magnitude away from an unthrottled brute force, and an attacker
# cannot cause a restart -- persisting counters would mean a DB write per failed
# login, which is not worth it for a single-user dashboard.
_attempts: dict[str, "_Failures"] = {}


@dataclass
class _Failures:
    count: int
    last_at: float
    locked_until: float


def seconds_until_unlocked(key: str) -> float:
    """Remaining lockout in seconds, or 0.0 when `key` may attempt a login."""
    entry = _attempts.get(key)
    if entry is None:
        return 0.0
    remaining = entry.locked_until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def register_failure(key: str, *, max_attempts: int, lockout_seconds: float) -> None:
    """Count one failed login and lock `key` out once it hits `max_attempts`."""
    now = time.monotonic()
    entry = _attempts.get(key)
    # Failures decay over the lockout duration so that a handful of typos spread
    # across months never adds up to a lockout; only a burst does.
    if entry is None or now - entry.last_at > lockout_seconds:
        entry = _Failures(count=0, last_at=now, locked_until=0.0)
        _attempts[key] = entry

    entry.count += 1
    entry.last_at = now
    if entry.count >= max_attempts:
        entry.locked_until = now + lockout_seconds


def clear(key: str) -> None:
    """Forget every failure for `key` -- called after a successful login."""
    _attempts.pop(key, None)


def reset_all() -> None:
    """Drop all state. Only used by tests, which share this module-level dict."""
    _attempts.clear()
