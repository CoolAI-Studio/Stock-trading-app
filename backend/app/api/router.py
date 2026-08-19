from fastapi import APIRouter

from app.api.routers import (
    alerts,
    auth,
    backtests,
    broker_costs,
    broker_credentials,
    indicators,
    market,
    notifications,
    orders,
    positions,
    risk,
    strategies,
    watchlist,
    webhooks,
    ws_ticket,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(alerts.router)
api_router.include_router(strategies.router)
api_router.include_router(backtests.router)
api_router.include_router(indicators.router)
api_router.include_router(market.router)
api_router.include_router(orders.router)
api_router.include_router(positions.router)
api_router.include_router(risk.router)
api_router.include_router(webhooks.router)
api_router.include_router(watchlist.router)
api_router.include_router(ws_ticket.router)
api_router.include_router(notifications.router)
api_router.include_router(broker_credentials.router)
api_router.include_router(broker_costs.router)
# app/main.py mounts this under the /api prefix. The health check and the
# raw /ws WebSocket route are mounted separately at root (see app/main.py)
# since neither is an /api resource.
