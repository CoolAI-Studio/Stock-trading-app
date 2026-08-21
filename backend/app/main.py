import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
    # Not in setup mode: the loop's first act is to query strategies, and the
    # database is one of the things that may not be configured yet. A worker
    # crash-looping behind a setup page would fill the log with the wrong error.
    run_worker = settings.WORKER_ENABLED and not setup_mode_active()
    worker_task = asyncio.create_task(run_forever(stop_event)) if run_worker else None

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

# SETUP MODE, and why the process no longer dies here.
#
# This used to be a bare `enforce_required_secrets(settings)`: a misconfigured
# deploy never bound a port and never served a request. That is the right
# instinct -- serving the real API with a forgeable JWT_SECRET is worse than
# serving nothing -- and the guarantee is unchanged below. What changed is who
# the deployment is for.
#
# The README hands a stranger two deploy buttons. render.yaml then asks them
# for seven values, two of which the old instructions produced by running a
# Python script on their own machine. Somebody who wants stock alerts on their
# phone does not have Python. They leave the blanks empty, the process dies at
# import, and all they get is a 502 and a stack trace in a log they will never
# find -- while the one thing that would unblock them, a page saying what is
# missing with a button that generates it, is exactly what a dead process
# cannot serve.
#
# So the failure is caught rather than fatal, and the app comes up in a mode
# that serves the setup endpoints AND NOTHING ELSE (see the middleware below).
# No login is possible, no token is minted, no worker runs, no database is
# touched. The security property the crash was defending is intact; the process
# just stays up long enough to explain itself.
#
# Every escape hatch is inherited unchanged, because the decision is still made
# by the same function: pytest skips it, ALLOW_INSECURE_SECRETS opts out.
try:
    enforce_required_secrets(settings)
    SETUP_MODE_REASON: str | None = None
except RuntimeError as exc:
    SETUP_MODE_REASON = str(exc)
    logging.getLogger("app.startup").error(
        "starting in SETUP MODE -- the API is locked until this is fixed: %s", exc
    )

app = FastAPI(title="Trading App API", lifespan=lifespan)


def setup_mode_active() -> bool:
    """Whether this process is locked to the setup endpoints."""
    return SETUP_MODE_REASON is not None


# Paths that still answer in setup mode. /healthz because the external watchdog
# is the only thing watching an unconfigured deployment, and it has to be told;
# the setup routes because they are the point.
_SETUP_MODE_OPEN = ("/api/setup", "/healthz")


@app.middleware("http")
async def _lock_until_configured(request, call_next):
    if not setup_mode_active():
        return await call_next(request)

    path = request.url.path
    if path == "/healthz":
        # Answered here rather than by the health router: that one opens a
        # database session through Depends, and a missing DATABASE_URL is one
        # of the things setup mode exists to report. A probe that 500s on the
        # way to saying 「not configured」 says nothing.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "fail",
                "checks": {
                    "setup": {
                        "status": "fail",
                        "detail": "尚未完成設定，API 已鎖住。請開啟前端頁面照指示填完設定。",
                    }
                },
            },
        )
    if path.startswith(_SETUP_MODE_OPEN[0]) or request.method == "OPTIONS":
        return await call_next(request)

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "這個部署還沒設定完成，所有功能都停用中。請先到設定頁完成設定。",
            "setup_required": True,
        },
    )


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
