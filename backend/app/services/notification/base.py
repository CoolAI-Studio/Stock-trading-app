from dataclasses import dataclass
from typing import Protocol


@dataclass
class SendResult:
    ok: bool
    error: str | None = None


class NotificationSender(Protocol):
    """Synchronous by design: every subscriber on services.events.bus runs
    on whatever thread publish() happens on (FastAPI's threadpool for a
    request handler, or the worker's own thread via asyncio.to_thread) --
    never the main event loop -- so a blocking HTTP/SMTP call here only
    blocks that one thread, not the app. No async bridging needed, unlike
    the WS broadcaster which must interact with a specific connection's
    event loop."""

    def send(self, config: dict, message: str) -> SendResult: ...
