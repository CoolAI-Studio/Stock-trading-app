from fastapi import APIRouter

from app.api.routers import auth

api_router = APIRouter()
api_router.include_router(auth.router)
# Further Phase 2+ resource routers (strategies, orders, ...) are included here.
# app/main.py mounts this under the /api prefix. The health check is mounted
# separately at root (see app/main.py) since it's an infra probe, not an API resource.
