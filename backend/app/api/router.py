from fastapi import APIRouter

from app.api.routers import (
    auth,
    market,
    orders,
    positions,
    risk,
    strategies,
    webhooks,
    ws_ticket,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(strategies.router)
api_router.include_router(market.router)
api_router.include_router(orders.router)
api_router.include_router(positions.router)
api_router.include_router(risk.router)
api_router.include_router(webhooks.router)
api_router.include_router(ws_ticket.router)
# Further Phase 6+ resource routers (notifications, ...) are included here.
# app/main.py mounts this under the /api prefix. The health check and the
# raw /ws WebSocket route are mounted separately at root (see app/main.py)
# since neither is an /api resource.
