import logging
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services import build_info, worker_health

logger = logging.getLogger("app.health")

router = APIRouter(tags=["health"])

_OK = "ok"
_FAIL = "fail"
_DISABLED = "disabled"
_STARTING = "starting"


def _check_database(db: Session) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # The reason is logged, never returned. Driver errors quote the whole
        # DSN -- host, user, sometimes the password -- and this endpoint is
        # unauthenticated, so the body only ever says which check failed.
        logger.exception("healthz: database check failed")
        return {"status": _FAIL, "detail": "query failed"}
    return {"status": _OK}


def _check_age(label: str, age_sec: float | None, uptime_sec: float) -> dict[str, Any]:
    if age_sec is None:
        # Nothing recorded yet. Render's free tier spins the whole process down
        # when idle, so the first probe after a cold start meets a worker that
        # has honestly never run -- a start, not an outage. Past the grace
        # window it means the worker never got going, which is an outage.
        in_grace = uptime_sec < settings.HEALTH_STARTUP_GRACE_SEC
        return {"status": _STARTING if in_grace else _FAIL}

    status_ = _FAIL if age_sec > settings.HEALTH_MAX_AGE_SEC else _OK
    return {"status": status_, label: round(age_sec, 1)}


@router.get("/healthz")
def healthz(response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Unauthenticated liveness probe; UptimeRobot hits it every 5 minutes.

    Deliberately more than {"status": "ok"}. Every serious failure mode this app
    has is silent -- a database that went away, a worker wedged mid-tick, a poll
    loop that raises on every symbol -- and a probe that cannot see them is a
    probe that never mails anyone. So each check runs for real and any failure
    turns the response into a 503, which is what actually triggers the alert.

    Cheap on purpose: one `SELECT 1` plus in-memory timestamps, nothing that
    touches a market-data provider.
    """
    checks: dict[str, dict[str, Any]] = {"database": _check_database(db)}

    if settings.WORKER_ENABLED:
        beat = worker_health.heartbeat.snapshot()
        # Two independent signals: the loop can keep spinning while every tick
        # raises, so "wedged" and "erroring" have to be distinguishable here.
        checks["worker"] = _check_age("last_loop_age_sec", beat.last_loop_age_sec, beat.uptime_sec)
        checks["market_data"] = _check_age(
            "last_poll_age_sec", beat.last_poll_age_sec, beat.uptime_sec
        )
        # Age alone said healthy through a total outage: the providers catch
        # every exception and return {}, so the loop went on completing polls
        # on schedule while not a single price came back. A run of empty
        # fetches is the only signal that distinguishes the two, and without
        # it UptimeRobot never mailed anyone.
        if (
            checks["market_data"]["status"] == _OK
            and beat.consecutive_empty_polls >= settings.HEALTH_MAX_EMPTY_POLLS
        ):
            checks["market_data"] = {
                "status": _FAIL,
                "consecutive_empty_polls": beat.consecutive_empty_polls,
            }

        # And the count above still cannot see ONE dead symbol among healthy
        # ones: a single good price clears the run. That is the likelier
        # shape by far -- a delisted stock, a typo that survived the input
        # checks, a name the feed stopped resolving -- and every alert on it
        # has stopped while the dashboard and this probe both look fine.
        #
        # Named, not merely counted: the owner's fix is to correct or delete
        # that row, and they cannot do either from 「something is wrong」.
        stale = sorted(
            symbol
            for symbol, gap in beat.symbol_gap_sec.items()
            if gap > settings.HEALTH_MAX_SYMBOL_GAP_SEC
        )
        # A COUNT, never the names. This endpoint is public by necessity --
        # render.yaml points its health check here and the external watchdog
        # polls it with no credentials -- so naming them served the owner's
        # watchlist to the internet at exactly the moment something went
        # wrong. A probe only needs to know that something is stale, and the
        # names are on the authenticated status page for whoever has to fix
        # it.
        checks["symbols"] = (
            {"status": _FAIL, "stale_count": len(stale)} if stale else {"status": _OK}
        )
    else:
        # An intentionally idle worker (local runs, the test suite) is a
        # configuration choice, not something to wake the owner over.
        checks["worker"] = {"status": _DISABLED}
        checks["market_data"] = {"status": _DISABLED}
        # Nothing is polling, so no symbol can have a price. The worker check
        # already says that; a second failure repeating it is noise.
        checks["symbols"] = {"status": _DISABLED}

    # A muted notifier is not a healthy one FOR THIS PRODUCT. WORKER_ENABLED
    # off is a configuration choice somebody makes to run the app locally;
    # NOTIFICATIONS_ENABLED off means every alert is discarded while everything
    # else -- the worker, the polling, the strategies -- goes on looking
    # perfectly well. Nothing anywhere surfaced it, and the 測試 button
    # positively reported success. The external watchdog is the only thing that
    # looks when nobody is looking, so this is what it needs to see.
    checks["notifications"] = (
        {"status": _OK}
        if settings.NOTIFICATIONS_ENABLED
        else {"status": _FAIL, "detail": "NOTIFICATIONS_ENABLED is off; no alert will be sent"}
    )

    failing = any(check["status"] == _FAIL for check in checks.values())
    if failing:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    # Which build answered. Deploys are automatic now, and the failure that
    # makes automatic dangerous is silent: a backend running code older than
    # main passes every check above, because an old build is not a sick one.
    # It rides on the unauthenticated probe because the moment you most need
    # it is the moment a deploy shipped something you cannot log in to.
    return {"status": _FAIL if failing else _OK, "version": build_info.version(), "checks": checks}
