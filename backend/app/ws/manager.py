import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("app.ws")


class ConnectionManager:
    """Keyed by user_id since every event we broadcast belongs to exactly
    one user's data. Guarded by a lock because connect/disconnect can race
    with a broadcast landing via asyncio.run_coroutine_threadsafe from a
    worker thread."""

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if sockets is None:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, message: dict) -> None:
        sockets = list(self._connections.get(user_id, ()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(user_id, ws)

    async def broadcast_to_all(self, message: dict) -> None:
        for user_id in list(self._connections):
            await self.send_to_user(user_id, message)


manager = ConnectionManager()
