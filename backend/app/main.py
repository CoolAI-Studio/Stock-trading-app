import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.routers.health import router as health_router
from app.config import Settings, enforce_required_secrets, settings
from app.logging_setup import configure_logging
from app.services import build_info
from app.services.events import bus
from app.services.market_loop import run_forever, shutdown_strategy_workers
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
    # 策略跑在子行程裡（#18）。不關的話它們會活過 app 本身——重新載入一次就多留
    # 三個，而這台機器只有 512 MB。
    shutdown_strategy_workers()
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


def docs_urls(config: Settings) -> dict[str, str | None]:
    """Where FastAPI serves its schema and docs -- or None, meaning nowhere.

    None makes the routes not exist, so a stranger gets 404 rather than 401.
    「這裡沒有這個東西」 tells them less than 「有，但你不能看」, and there is
    nothing to gain from the distinction here.
    """
    if config.ENABLE_API_DOCS:
        return {"openapi_url": "/openapi.json", "docs_url": "/docs", "redoc_url": "/redoc"}
    return {"openapi_url": None, "docs_url": None, "redoc_url": None}


app = FastAPI(title="Trading App API", lifespan=lifespan, **docs_urls(settings))


def boot_problem() -> str | None:
    """A boot-time database failure, or None.

    scripts/start.py runs the migration and, when it cannot, records the reason
    here instead of exiting -- because exiting is what used to leave the
    deployer with a dead URL (see that file's docstring).

    Read from the environment at CALL TIME rather than captured at import, so a
    test can set it and so the value belongs to the process that actually
    booted this way.

    WHY IT COUNTS AS SETUP MODE. The hosting platform's health check points at
    /healthz, and a first deploy has no previous version to fall back to: a
    probe that never passes is a deploy marked FAILED, which takes down the
    setup page at exactly the moment it is the only useful thing in the app.
    A migration that could not run at boot means this deployment has never
    worked -- the schema may not even exist -- and that is 「still being set
    up」, not 「a working system broke」. The second one still answers 503,
    because the watchdog depends on it.
    """
    return (os.environ.get("DATABASE_MIGRATION_ERROR") or "").strip() or None


def setup_mode_active() -> bool:
    """Whether this process is locked to the setup endpoints."""
    return SETUP_MODE_REASON is not None or boot_problem() is not None


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
        # 200, NOT 503, and this is the whole reason a first deploy works.
        #
        # render.yaml points healthCheckPath here. A first deploy has no
        # previous version to fall back to, so a probe that never passes is a
        # deploy Render marks as FAILED -- and the setup page that exists to
        # explain what is missing goes down with it, at exactly the moment it
        # is the only useful thing in the app. Measured on a blank deployment
        # by scripts/deploy_smoke.py; this was one of three places a new user
        # stopped.
        #
        # 503 was chosen so the external watchdog would notice, and that
        # concern is real -- 「警告不能停擺」 needs an outside observer. It is
        # answered by the BODY instead: `status: "setup"` is neither "ok" nor
        # "fail", so the watchdog can say 「still being set up」 without a
        # hosting platform reading it as a dead container.
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "setup",
                # Carried here too: a first deploy has nothing else to look
                # at, and 「is this even the build I just pushed?」 is the
                # question somebody staring at a setup screen is asking.
                "version": build_info.version(),
                "checks": {
                    "setup": {
                        "status": "setup",
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


@app.middleware("http")
async def _setup_is_reachable_from_anywhere(request, call_next):
    """The setup endpoints answer any origin. This is the trap it exists for.

    A wrong CORS_ORIGINS makes the browser discard every response from this
    backend -- INCLUDING the setup page's own. So the one page whose entire job
    is to explain the misconfiguration gets blanked by the misconfiguration,
    and what the owner sees is an empty screen with the reason buried in a
    developer console they will never open. It is the single most likely
    mistake in the deploy flow, because the frontend's URL cannot be known
    until after the frontend exists.

    Safe to open because of what is behind it and nothing else: these routes
    carry no secrets, no user data and no credentials -- they report WHICH
    settings are blank and hand out freshly generated random values, and they
    404 entirely once there is nothing left to configure.

    The specific origin is echoed rather than "*": credentials are irrelevant
    here, but a wildcard would also apply to a preflight the browser then
    caches for the whole origin, and being narrow costs nothing.
    """
    origin = request.headers.get("origin")
    if not origin or not request.url.path.startswith("/api/setup"):
        return await call_next(request)

    if request.method == "OPTIONS":
        # Answered here rather than passed down: the CORS middleware above will
        # only answer a preflight for an origin it already allows, which is
        # exactly the origin this is for.
        response = Response(status_code=status.HTTP_200_OK)
    else:
        response = await call_next(request)

    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "content-type"
    response.headers["Vary"] = "Origin"
    return response


app.include_router(health_router)
app.include_router(ws_router)
app.include_router(api_router, prefix="/api")
