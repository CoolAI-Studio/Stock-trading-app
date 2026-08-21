"""One page that answers 「是不是還在跑」 without leaving the app.

CLAUDE.md asks for a Prometheus endpoint and a Grafana dashboard, and gives the
reason: 「警告不能停擺，就必須看得到它有沒有在跑」. The reason is right; those
instruments are wrong for who this is now for.

A metrics endpoint is only worth having if something scrapes it, and on a
free-tier Render box nothing does. Making it real means a Grafana Cloud
account, push credentials, and an eighth blank in a deploy form -- for a
dashboard that somebody who wants stock alerts on their phone will never open.
It buys a screen the audience does not want at the cost of the thing they need
most, which is being able to start.

Everything those dashboards would plot is already inside this process:
services/worker_health holds the heartbeat, the run of empty polls and the
per-symbol gaps; notification_logs holds what happened to every alert ever
raised. This assembles them. No third party, no account, no extra blank, and it
works on every deployment from the moment it boots.

NOT /healthz, WHICH STAYS AS IT IS. That one is unauthenticated, so it is
deliberately terse: 「fail」 with no numbers, because anyone can read it. This
one is behind a login and can therefore say how long, how many, and which.
"""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.config import settings
from app.db.session import get_db
from app.models.enums import NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationLog
from app.models.user import User
from app.services import setup_state, worker_health
from app.services.ai_provider import get_ai_provider

router = APIRouter(prefix="/system", tags=["system"])

# How far back the notification counts look. A lifetime total stops moving and
# stops meaning anything; what the owner is asking is 「is it working NOW」, and
# a day covers a full trading session on both sides of the world plus the
# overnight the retry ladder can span.
_WINDOW_HOURS = 24

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"


# What the assistant is for, and what it is not. Without one of its own it
# inherits the broker-credential prompt in ai_provider/base.py and answers
# every question as though it were about formatting an API key.
_ASSISTANT_PROMPT = (
    "你是一個內建在個人股票提醒系統裡的診斷助手。使用者不是工程師。"
    "你會收到這個部署「現在」的實際狀態，請根據那些數字回答他的問題，"
    "並且用他能照著做的步驟講（例如「去 Render 按 Manual Deploy」、"
    "「把這個代號從追蹤清單刪掉」）。"
    "不要憑空猜測狀態裡沒有的東西；狀態裡看不出來的就說看不出來。"
    "你不能下單、不能執行程式、也讀不到使用者的帳戶內容，只能給文字說明。"
    "全程使用繁體中文。"
)


def _assistant_available() -> bool:
    """Whether asking would produce an answer rather than an error.

    Reported on the status payload so the UI can leave the box out entirely.
    AI_API_KEY is one more blank in a deploy form and is optional by design; a
    page that offers a feature which answers every question with 「尚未設定」
    makes the optional thing feel broken.
    """
    return bool(settings.AI_API_KEY.strip() and settings.AI_MODEL.strip())


def _worse(current: str, candidate: str) -> str:
    order = {_OK: 0, _WARN: 1, _FAIL: 2}
    return candidate if order[candidate] > order[current] else current


def _notification_counts(db: Session, user_id: int) -> dict[str, Any]:
    """What actually reached the owner, and what did not, in the last day.

    The four buckets are the ones NotificationLog.delivery_state already
    defines, for the reason it defines them: 「failed」 on its own is four
    different situations wearing one word, and only one of them means anything
    is still going to happen.
    """
    since = utcnow() - timedelta(hours=_WINDOW_HOURS)
    rows = db.execute(
        select(
            NotificationLog.status,
            NotificationLog.attempts,
            NotificationLog.next_retry_at,
            NotificationLog.channel_id,
            func.count(NotificationLog.id),
        )
        .where(NotificationLog.user_id == user_id, NotificationLog.created_at >= since)
        .group_by(
            NotificationLog.status,
            NotificationLog.attempts,
            NotificationLog.next_retry_at,
            NotificationLog.channel_id,
        )
    ).all()

    counts = {"sent": 0, "retrying": 0, "deferred": 0, "given_up": 0, "reached_nobody": 0}
    for status_, attempts, next_retry_at, channel_id, count in rows:
        if status_ == NotificationStatus.SENT:
            counts["sent"] += count
        elif channel_id is None:
            # There was nowhere to send it. Folding this into 「failed」 would
            # hide the one failure the owner can actually fix -- and an alert
            # that reached nobody used to leave no trace at all.
            counts["reached_nobody"] += count
        elif next_retry_at is None:
            counts["given_up"] += count
        elif not attempts:
            counts["deferred"] += count
        else:
            counts["retrying"] += count

    counts["window_hours"] = _WINDOW_HOURS
    return counts


@router.get("/status")
def system_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    beat = worker_health.heartbeat.snapshot()
    overall = _OK

    worker: dict[str, Any] = {
        "enabled": settings.WORKER_ENABLED,
        "uptime_sec": round(beat.uptime_sec, 1),
        "last_loop_age_sec": (
            None if beat.last_loop_age_sec is None else round(beat.last_loop_age_sec, 1)
        ),
        "last_poll_age_sec": (
            None if beat.last_poll_age_sec is None else round(beat.last_poll_age_sec, 1)
        ),
    }
    if settings.WORKER_ENABLED:
        stalled = (
            beat.last_loop_age_sec is not None
            and beat.last_loop_age_sec > settings.HEALTH_MAX_AGE_SEC
        )
        # Never having run is only 「starting」 inside the grace window; past it
        # the worker never got going, which is the same outage.
        never_ran = beat.last_loop_age_sec is None and (
            beat.uptime_sec >= settings.HEALTH_STARTUP_GRACE_SEC
        )
        if stalled or never_ran:
            overall = _worse(overall, _FAIL)

    market_data: dict[str, Any] = {
        "consecutive_empty_polls": beat.consecutive_empty_polls,
        # Named and aged, which is the detail /healthz cannot carry. A symbol
        # that never resolves is a symbol whose alerts have silently stopped,
        # and it is fixed by correcting or deleting that one row.
        "stale_symbols": [
            {"symbol": symbol, "gap_sec": round(gap, 1)}
            for symbol, gap in sorted(beat.symbol_gap_sec.items())
        ],
    }
    if settings.WORKER_ENABLED:
        if beat.consecutive_empty_polls >= settings.HEALTH_MAX_EMPTY_POLLS:
            overall = _worse(overall, _FAIL)
        if any(gap > settings.HEALTH_MAX_SYMBOL_GAP_SEC for gap in beat.symbol_gap_sec.values()):
            overall = _worse(overall, _FAIL)
        elif beat.symbol_gap_sec:
            # Below the threshold it is a hiccup, not an outage -- but it is
            # the shape an outage starts as, and hiding it until it crosses a
            # line is how somebody finds out too late.
            overall = _worse(overall, _WARN)

    notifications = _notification_counts(db, user.id)
    notifications["enabled"] = settings.NOTIFICATIONS_ENABLED
    if not settings.NOTIFICATIONS_ENABLED:
        # A muted notifier is not a healthy one FOR THIS PRODUCT: every alert
        # is discarded while the worker, the polling and the strategies all go
        # on looking perfectly well. Same judgement /healthz makes.
        overall = _worse(overall, _FAIL)
    if notifications["reached_nobody"] or notifications["given_up"]:
        overall = _worse(overall, _WARN)

    return {
        "overall": overall,
        "worker": worker,
        "market_data": market_data,
        "notifications": notifications,
        "assistant_available": _assistant_available(),
    }


class AssistRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("請先輸入問題。")
        return value.strip()


class AssistResult(BaseModel):
    ok: bool
    reply: str | None = None
    error: str | None = None


def _state_for_assistant(db: Session, user: User) -> str:
    """This deployment's own facts, in a form a model can reason over.

    NOTHING SECRET GOES IN. Counts, ages, booleans, and the NAMES of settings
    that are still blank -- never the value of any of them. This is sent to a
    third party, so what goes has to be something the owner would be
    comfortable reading out loud. The name is what makes the advice
    actionable; the value would be the deployment itself.
    """
    status = system_status(db=db, user=user)
    missing = [item.name for item in setup_state.missing_settings(settings)]
    worker = status["worker"]
    market = status["market_data"]
    notifications = status["notifications"]

    stale = ", ".join(
        f"{row['symbol']}（已 {row['gap_sec']:.0f} 秒沒有價格）" for row in market["stale_symbols"]
    )
    return (
        f"這個部署目前的狀態：\n"
        f"- 整體：{status['overall']}\n"
        f"- 背景 worker 啟用：{worker['enabled']}；"
        f"最後一次循環：{worker['last_loop_age_sec']} 秒前；"
        f"最後一次成功抓行情：{worker['last_poll_age_sec']} 秒前\n"
        f"- 連續抓不到任何價格的次數：{market['consecutive_empty_polls']}\n"
        f"- 抓不到報價的代號：{stale or '無'}\n"
        f"- 通知功能啟用：{notifications['enabled']}\n"
        f"- 最近 {notifications['window_hours']} 小時的通知："
        f"已送出 {notifications['sent']}、還在重試 {notifications['retrying']}、"
        f"等靜音 {notifications['deferred']}、已放棄 {notifications['given_up']}、"
        f"沒送到任何管道 {notifications['reached_nobody']}\n"
        f"- 還沒填的設定項目（只列名稱，不含內容）：{', '.join(missing) or '無'}"
    )


@router.post("/assist", response_model=AssistResult)
def system_assist(
    payload: AssistRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> AssistResult:
    """The question a non-developer actually asks -- 「something is wrong and I
    do not know what」 -- answered against this deployment rather than in
    general.

    Deliberately NOT part of the setup flow: the assistant needs AI_API_KEY,
    which is itself a blank somebody has to fill, so a setup that depended on
    it would be circular. Setup is explained by the setup page; this is for
    afterwards.
    """
    question = f"{payload.message}\n\n{_state_for_assistant(db, user)}"
    result = get_ai_provider().ask(question, system=_ASSISTANT_PROMPT)
    return AssistResult(ok=result.ok, reply=result.reply, error=result.error)
