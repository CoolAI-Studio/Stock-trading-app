import secrets
import time

# In-memory and process-local -- consistent with the `--workers 1`
# constraint already required by the market-data worker (app/services/
# market_loop.py). A ticket is single-use: POST /api/ws/ticket issues it
# over an authenticated REST call, then the WS handshake immediately pops
# it, so a leaked URL in a proxy log is dead within its TTL.
_tickets: dict[str, tuple[int, float]] = {}


def issue_ticket(user_id: int, ttl_seconds: float) -> str:
    _prune()
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (user_id, time.monotonic() + ttl_seconds)
    return ticket


def redeem_ticket(ticket: str) -> int | None:
    """Single-use: pops the ticket regardless of outcome. Returns the
    user_id, or None if the ticket is unknown or expired."""
    _prune()
    entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return user_id


def _prune() -> None:
    now = time.monotonic()
    expired = [t for t, (_, expires_at) in _tickets.items() if expires_at < now]
    for t in expired:
        _tickets.pop(t, None)
