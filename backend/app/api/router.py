from fastapi import APIRouter

api_router = APIRouter()
# Phase 1+ resource routers (auth, strategies, orders, ...) are included here.
# app/main.py mounts this under the /api prefix. The health check is mounted
# separately at root (see app/main.py) since it's an infra probe, not an API resource.
