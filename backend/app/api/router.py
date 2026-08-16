from fastapi import APIRouter

from app.api.routers import auth, market, strategies

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(strategies.router)
api_router.include_router(market.router)
# Further Phase 3+ resource routers (orders, positions, risk-settings, ...)
# are included here. app/main.py mounts this under the /api prefix. The
# health check is mounted separately at root (see app/main.py) since it's an
# infra probe, not an API resource.
