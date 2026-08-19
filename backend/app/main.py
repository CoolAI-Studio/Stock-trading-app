import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routers.health import router as health_router
from app.config import enforce_required_secrets, settings
from app.logging_setup import configure_logging
from app.services.events import bus
from app.services.market_loop import run_forever
from app.services.notification.dispatcher import handle_event as dispatch_notification
from app.ws.broadcast import broadcaster
from app.ws.routes import router as ws_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    broadcaster.bind_loop(asyncio.get_running_loop())
    bus.subscribe(broadcaster.handle_event)
    if settings.NOTIFICATIONS_ENABLED:
        bus.subscribe(dispatch_notification)

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_forever(stop_event)) if settings.WORKER_ENABLED else None

    yield

    if worker_task is not None:
        stop_event.set()
        await worker_task
    bus.unsubscribe(broadcaster.handle_event)
    bus.unsubscribe(dispatch_notification)


# At import, before anything else runs, so the guard below and every module
# imported after this point can actually say something. Configured here rather
# than in the lifespan for the same reason: a failure during startup is
# exactly the one worth having a log line for.
configure_logging(settings.LOG_LEVEL)

# At import, not in lifespan: uvicorn then dies while loading the app, so a
# misconfigured deploy never binds a port and never serves a single request.
enforce_required_secrets(settings)

app = FastAPI(title="Trading App API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(ws_router)
app.include_router(api_router, prefix="/api")
