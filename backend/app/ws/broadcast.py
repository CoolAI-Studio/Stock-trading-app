import asyncio
from datetime import UTC, datetime

from app.services.events import Event
from app.ws.manager import manager

# Events without a user_id in their data aren't user-scoped (a symbol quote
# is the same for everyone watching it) -- these types get fanned out to
# every connected client instead of being dropped.
_BROADCAST_TO_ALL_EVENT_TYPES = {"quote.update"}


def _to_message(event: Event) -> dict:
    return {
        "type": event.type,
        "ts": datetime.now(UTC).isoformat(),
        "v": 1,
        "data": event.data,
    }


class WsBroadcaster:
    """Bridges the synchronous services.events.bus to the async
    ConnectionManager. `handle_event` is registered as a plain sync
    subscriber (see app/services/events.py's design) and is called from
    whatever thread publish() happens on -- a request handler running in
    FastAPI's threadpool, or the worker's background thread via
    asyncio.to_thread. asyncio.run_coroutine_threadsafe is what makes it
    safe to call into the manager's async methods from there."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def handle_event(self, event: Event) -> None:
        if self._loop is None:
            return

        user_id = event.data.get("user_id")
        if user_id is not None:
            coro = manager.send_to_user(user_id, _to_message(event))
        elif event.type in _BROADCAST_TO_ALL_EVENT_TYPES:
            coro = manager.broadcast_to_all(_to_message(event))
        else:
            return

        asyncio.run_coroutine_threadsafe(coro, self._loop)


broadcaster = WsBroadcaster()
