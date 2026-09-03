import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.enums import NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationLog
from app.services import build_info, worker_health

# 只看最近這段時間的「放棄」。跟狀態頁的統計窗口同一個道理：半個月前沒送出去的那
# 一則，今天已經不是一個他可以做什麼的東西，而一個永遠紅著的燈會被學會忽略。
_UNDELIVERED_WINDOW_HOURS = 24

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
def healthz(
    response: Response,
    deep: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Unauthenticated liveness probe; UptimeRobot hits it every 5 minutes.

    ＊ 兩個讀者，兩件不同的事。

    這支端點有兩個人在看，而他們拿狀態碼去做的事完全不同：

        Render（render.yaml 的 healthCheckPath）→ 要不要**重開這台機器**
        看門狗／UptimeRobot                      → 要不要**寄信給人**

    Render 的文件（2026-09 讀的）：已經在跑的服務，健康檢查失敗 15 秒就先把流量拔
    掉，失敗 60 秒「Render automatically restarts the instance」；部署當中 15 分鐘
    內沒有全部通過就「Render cancels the deploy」。

    而這裡有好幾格是**重開機修不好、也不會自己好**的：一顆下架的代號、
    NOTIFICATIONS_ENABLED 被關掉、上游整個掛掉、24 小時內有一則放棄的提醒。任何一
    個成立，原本的規則就會讓這台機器每十幾分鐘被重開一次，永遠——而重開的代價正是
    策略池被丟掉、暖身重來、那幾秒服務是斷的。**為了保護提醒而做的探測，把提醒弄停
    了。** 部署當中那條更狠：他從此收不到任何更新，包含安全修補。

    所以狀態碼分兩種，內容完全一樣：

        沒有參數      503 只留給「重開這個行程有機會修好」的（資料庫連不上、迴圈卡
                      死、輪詢沒完成）。
        ?deep=1       任何一格 fail 就 503。

    **預設是淺的，這一點是刻意的。** 已經在跑的那些服務，後台的 healthCheckPath 是
    建立當下抄過去的一份（#53 的教訓），改 render.yaml 追不回去——所以那個沒有參數的
    網址必須自己就是安全的。深的那一條要由我們控制得到的東西去指（watchdog.py、文
    件、系統狀態頁上印出來的那一串）。

    這個判斷這個 repo 做過一次，只是做窄了：test_first_deploy_comes_up 已經寫著
    「a probe that never passes is a deploy Render marks as FAILED」，所以設定模式
    回 200。那個推論對，範圍太小。

    Deliberately more than {"status": "ok"}. Every serious failure mode this app
    has is silent -- a database that went away, a worker wedged mid-tick, a poll
    loop that raises on every symbol -- and a probe that cannot see them is a
    probe that never mails anyone. So each check runs for real and any failure
    turns the response into a 503, which is what actually triggers the alert.

    Cheap on purpose: one `SELECT 1` plus in-memory timestamps, nothing that
    touches a market-data provider.
    """
    checks: dict[str, dict[str, Any]] = {"database": _check_database(db)}
    # 重開這個行程有沒有機會修好。只有這個決定沒帶參數的那一條要不要 503。
    restart_might_help = checks["database"]["status"] == _FAIL

    if settings.WORKER_ENABLED:
        beat = worker_health.heartbeat.snapshot()
        # Two independent signals: the loop can keep spinning while every tick
        # raises, so "wedged" and "erroring" have to be distinguishable here.
        checks["worker"] = _check_age("last_loop_age_sec", beat.last_loop_age_sec, beat.uptime_sec)
        checks["market_data"] = _check_age(
            "last_poll_age_sec", beat.last_poll_age_sec, beat.uptime_sec
        )
        # **在下面那個覆寫之前記下來。** 這兩格失敗的意思是「這個行程的迴圈沒有在
        # 轉」，那正是重開機修得好的一種；而底下的連續空輪詢會用同一個鍵覆寫掉它，
        # 意思卻完全不同（上游掛了，重開我們這台不會讓 Yahoo 回來）。
        restart_might_help = restart_might_help or _FAIL in (
            checks["worker"]["status"],
            checks["market_data"]["status"],
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

        # 迴圈在轉、行情抓得到、每個代號都有價——而使用者的策略一支都跑不起來。
        #
        # 上面每一項都看不到這件事：策略跑在子行程裡，而「子行程壞掉不是策略的錯」
        # （#18）刻意讓它不累積、不停用，於是它也不留下任何痕跡。那個狀態就是提醒
        # 全面停擺，而這支探測是唯一會在沒有人看的時候說話的東西。
        blind = sorted(
            strategy_id
            for strategy_id, blocked_sec in beat.strategy_blocked_sec.items()
            if blocked_sec > settings.HEALTH_MAX_STRATEGY_BLOCKED_SEC
        )
        # 數量，不是 id——跟上面的代號同一條規則：這支端點沒有憑證也打得到。
        checks["strategies"] = (
            {"status": _FAIL, "blocked_count": len(blind)} if blind else {"status": _OK}
        )

        # 報價回得來、K 棒回不來——上游是不同的端點，所以這是一個真的組合，而上面每
        # 一項都看不到它：迴圈在轉、輪詢有價、子行程好好的，而每一支 on_bar 策略一則
        # 提醒都沒發出。
        #
        # 門檻沿用代號那一格的，因為問的是同一件事：這個代號多久沒有拿到資料了。
        stuck = sorted(
            series
            for series, gap in beat.bar_gap_sec.items()
            if gap > settings.HEALTH_MAX_SYMBOL_GAP_SEC
        )
        # 數量，不是名字——這支端點沒有憑證也打得到。
        checks["bars"] = {"status": _FAIL, "stale_count": len(stuck)} if stuck else {"status": _OK}
    else:
        # An intentionally idle worker (local runs, the test suite) is a
        # configuration choice, not something to wake the owner over.
        checks["worker"] = {"status": _DISABLED}
        checks["market_data"] = {"status": _DISABLED}
        # Nothing is polling, so no symbol can have a price. The worker check
        # already says that; a second failure repeating it is noise.
        checks["symbols"] = {"status": _DISABLED}
        checks["strategies"] = {"status": _DISABLED}
        checks["bars"] = {"status": _DISABLED}

    # A muted notifier is not a healthy one FOR THIS PRODUCT. WORKER_ENABLED
    # off is a configuration choice somebody makes to run the app locally;
    # NOTIFICATIONS_ENABLED off means every alert is discarded while everything
    # else -- the worker, the polling, the strategies -- goes on looking
    # perfectly well. Nothing anywhere surfaced it, and the 測試 button
    # positively reported success. The external watchdog is the only thing that
    # looks when nobody is looking, so this is what it needs to see.
    # **一則沒有送到的提醒，是這個產品的重大失效**（CLAUDE.md 第一段）。而這一格原本
    # 只問了「NOTIFICATIONS_ENABLED 有沒有被關掉」——也就是問「這個功能在不在」，不是
    # 問「提醒有沒有送到」。
    #
    # 於是這個情境是全綠的：他的 bot token 被撤銷 → 每一則都失敗 → 重送到期、放棄 →
    # /healthz 全綠 → 看門狗永遠不寄信 → 他什麼都不知道。跟子行程停擺、K 棒抓不到是
    # 同一個形狀，只是輪到最重要的那一格。
    #
    # **算的是「放棄」，不是「失敗」。** 失敗會重送，而重送多半會成功——一次 Telegram
    # 抖動不該把看門狗叫起來。放棄不一樣：重送已經用完，那一則永遠不會到了。
    #
    # 只看最近這段時間：半個月前放棄掉的那一則不該讓今天的探測是紅的，而一個永遠紅著
    # 的燈會被學會忽略。
    undelivered = 0
    if settings.NOTIFICATIONS_ENABLED:
        try:
            since = utcnow() - timedelta(hours=_UNDELIVERED_WINDOW_HOURS)
            undelivered = int(
                db.execute(
                    select(func.count(NotificationLog.id)).where(
                        NotificationLog.status == NotificationStatus.FAILED,
                        NotificationLog.next_retry_at.is_(None),
                        NotificationLog.channel_id.is_not(None),
                        NotificationLog.created_at >= since,
                    )
                ).scalar_one()
                or 0
            )
        except Exception:  # noqa: BLE001 -- 資料庫那一格已經在報這件事了，不要報兩次
            undelivered = 0

    if not settings.NOTIFICATIONS_ENABLED:
        checks["notifications"] = {
            "status": _FAIL,
            "detail": "NOTIFICATIONS_ENABLED is off; no alert will be sent",
        }
    elif undelivered:
        # 幾則，不是哪一則。這支端點沒有憑證也打得到——跟代號和策略那兩格同一條規則。
        checks["notifications"] = {"status": _FAIL, "undelivered": undelivered}
    else:
        checks["notifications"] = {"status": _OK}

    failing = any(check["status"] == _FAIL for check in checks.values())
    if failing if deep else restart_might_help:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    # Which build answered. Deploys are automatic now, and the failure that
    # makes automatic dangerous is silent: a backend running code older than
    # main passes every check above, because an old build is not a sick one.
    # It rides on the unauthenticated probe because the moment you most need
    # it is the moment a deploy shipped something you cannot log in to.
    return {"status": _FAIL if failing else _OK, "version": build_info.version(), "checks": checks}
