import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("app.events")


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """In-process pub/sub. Synchronous by design: Phase 3 ships only a
    logging subscriber, called directly in whatever thread publishes (a sync
    request handler in FastAPI's threadpool, or the worker's own thread).
    A later async subscriber (e.g. a WS broadcaster) is responsible for its
    own thread/loop bridging inside its own handler -- publish() call sites
    in signals.py / market_loop.py / the webhook router never need to
    change when that's added."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable[[Event], None]) -> None:
        try:
            self._subscribers.remove(handler)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        for handler in list(self._subscribers):
            try:
                handler(event)
            except Exception:
                logger.exception("event handler failed for %s", event.type)


def _log_subscriber(event: Event) -> None:
    logger.info("event: %s %s", event.type, event.data)


bus = EventBus()
bus.subscribe(_log_subscriber)
