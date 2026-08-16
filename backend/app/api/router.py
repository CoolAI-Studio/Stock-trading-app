from fastapi import APIRouter

from app.api.routers import auth, market, orders, positions, risk, strategies

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(strategies.router)
api_router.include_router(market.router)
api_router.include_router(orders.router)
api_router.include_router(positions.router)
api_router.include_router(risk.router)
# Further Phase 4+ resource routers (tradingview webhook, ws ticket,
# notifications, ...) are included here. app/main.py mounts this under the
# /api prefix. The health check is mounted separately at root (see
# app/main.py) since it's an infra probe, not an API resource.
