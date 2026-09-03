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

import os
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.config import settings
from app.db.session import get_db
from app.enums import NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationLog
from app.models.position import Position
from app.models.strategy import Strategy
from app.models.user import User
from app.services import (
    ai_settings,
    build_info,
    hosting,
    setup_state,
    update_check,
    worker_health,
)
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
    # **不要在這裡寫死任何一家平台的名字。** 原本的範例是「去 Render 按 Manual
    # Deploy」，那是在教模型用 Render 的說法回答每一個使用者——而安裝頁給了四條路。
    # 對 Fly.io 的使用者說「Render 後台」比含糊更糟：他會真的去找那一頁。平台名稱由
    # 底下那份狀態帶進來（_state_for_assistant），這裡只說「照狀態裡那一家講」。
    "並且用他能照著做的步驟講（例如「把這個代號從追蹤清單刪掉」）。"
    "要他去部署平台上操作的時候，**用狀態裡寫的那一家的說法**，不要假設是哪一家。"
    "不要憑空猜測狀態裡沒有的東西；狀態裡看不出來的就說看不出來。"
    "你不能下單、不能執行程式、也讀不到使用者的帳戶內容，只能給文字說明。"
    "全程使用繁體中文。"
)


def _assistant_available(db: Session, user_id: int) -> bool:
    """Whether asking would produce an answer rather than an error.

    Resolved per user rather than read off the environment: the key now lives
    in the database (services/ai_settings.py) with the env var as a fallback,
    and a page that advertised the assistant based on the wrong one would
    either hide a working feature or offer a broken one.

    Reported on the status payload so the UI can leave the box out entirely. An
    AI key is optional by design; a box that answers every question with
    「尚未設定」 makes the optional thing feel broken.
    """
    return ai_settings.resolve(db, user_id).is_configured


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
    # Annotated 而不是 `= Query(...)`，是刻意的：這個函式**也被 system_assist 直接
    # 呼叫**，而 FastAPI 的預設值只有走路由時才會被解析。寫成 `= Query(...)` 的
    # 話，直接呼叫拿到的是那個哨兵物件本身，然後它會一路流到正規表達式裡炸掉——
    # 而那正是這次改動一開始造成的七條紅。
    #
    # Annotated 讓純粹的預設值就是 None，所以兩條路都對。
    frontend_commit: Annotated[
        str | None,
        Query(
            max_length=40,
            description="前端這一份的 commit。它是建置期常數，後端不知道，所以由前端帶上來。",
        ),
    ] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    beat = worker_health.heartbeat.snapshot()
    overall = _OK

    # 他這一份是不是舊的。
    #
    # 刻意**不影響 overall**：落後不是故障。這一頁的紅燈是給「提醒現在停擺了」用
    # 的，而把「有新版」也算進去會讓那個紅燈失去意義——他會學會忽略它。
    update = update_check.status()
    # 前端自己的 commit 只有前端知道（它是建置期常數），所以那個問題由它帶上來問。
    #
    # **落後和分岔是兩件事**：前者按一次重新部署就好，後者按幾次都沒有用，因為同步
    # 根本沒跑。說錯的話他會重試幾次然後放棄，而真正該告訴他的那件事從頭到尾沒說出
    # 口。
    #
    # 沒帶的時候還有一個地方問得到：**這一份自己供應畫面的話，前端就是這個映像檔裡
    # 的那一份**，跟後端同一個 commit，依建構為真（#53）。那條路不需要平台把
    # APP_GIT_COMMIT 當 build arg 傳進來——而那是一個平台不見得會做、我們也保證不了
    # 的動作，少了它這個偵測會整個安靜地消失。
    #
    # 前端有帶就以它為準：分開部署的那一份可能比後端舊好幾個月，而那正是要抓的情
    # 況；拿後端的去回答會把它蓋掉，方向還是「看起來沒問題」的那一邊。
    # 函式內匯入：app.main 匯入這個 router，模組層再匯回去就是循環。到這一行的時候
    # main 已經載完了，而且這樣測試 monkeypatch main.FRONTEND_DIST 也看得到。
    from app import main

    asked_about = frontend_commit
    if not asked_about and main.FRONTEND_DIST.is_dir():
        asked_about = build_info.commit()
    update["frontend_from_upstream"] = (
        update_check.is_from_upstream(asked_about) if asked_about else None
    )

    worker: dict[str, Any] = {
        "enabled": settings.WORKER_ENABLED,
        "uptime_sec": round(beat.uptime_sec, 1),
        "last_loop_age_sec": (
            None if beat.last_loop_age_sec is None else round(beat.last_loop_age_sec, 1)
        ),
        "last_poll_age_sec": (
            None if beat.last_poll_age_sec is None else round(beat.last_poll_age_sec, 1)
        ),
        # 這個行程起來之前，有多久沒有任何行程在跑。
        #
        # **刻意不影響 overall，也刻意不上 /healthz。** 那個洞已經過去了——現在是醒
        # 著的。紅燈是給「現在停擺了」用的，而看門狗每 15 分鐘打一次、免費方案本來
        # 就會反覆休眠，每次醒來寄一封信，收件匣一天就會被塞到他不再看。
        "slept_sec": (None if beat.slept_sec is None else round(beat.slept_sec, 1)),
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

    # YOUR symbols, not the process's. The heartbeat is a module-level
    # singleton and its map is the union across every account -- so this used
    # to hand each caller everybody else's watch list, named, on a page that
    # otherwise filters correctly (the notification counts three lines below
    # have always been scoped by user_id).
    #
    # What somebody is watching is one of the most personal things in this
    # app, and it was being shown to anyone else with an account.
    owned_symbols = {
        symbol
        for (symbol,) in db.query(Strategy.symbol).filter(Strategy.user_id == user.id).distinct()
    } | {
        symbol
        for (symbol,) in db.query(Position.symbol).filter(Position.user_id == user.id).distinct()
    }
    own_gaps = {
        symbol: gap for symbol, gap in beat.symbol_gap_sec.items() if symbol in owned_symbols
    }
    own_bar_gaps = {
        series: gap
        for series, gap in beat.bar_gap_sec.items()
        if series.split(" ")[0] in owned_symbols
    }

    market_data: dict[str, Any] = {
        "consecutive_empty_polls": beat.consecutive_empty_polls,
        # Named and aged, which is the detail /healthz cannot carry. A symbol
        # that never resolves is a symbol whose alerts have silently stopped,
        # and it is fixed by correcting or deleting that one row.
        "stale_symbols": [
            {"symbol": symbol, "gap_sec": round(gap, 1)} for symbol, gap in sorted(own_gaps.items())
        ],
        # 同一個代號的日線好好的、週線抓不到，是常見的形狀，所以這一格跟上面那格分開。
        # 一樣**只列你自己的**：心跳的表是跨全部帳號的聯集。
        #
        # 按代號濾（鍵的前半段），不按週期：週期活在編譯出來的策略上，不在資料列上，
        # 而「這個代號是不是你的」已經足夠把別人的東西擋掉。
        "stale_bars": [
            {"series": series, "gap_sec": round(gap, 1)}
            for series, gap in sorted(own_bar_gaps.items())
        ],
    }
    # 哪幾支策略叫不動它的子行程。**只列你自己的**，跟 stale_symbols 同一個理由：
    # 心跳是行程層級的單例，它的表是跨全部帳號的聯集。
    own_strategies = {
        sid: name
        for sid, name in db.query(Strategy.id, Strategy.name).filter(Strategy.user_id == user.id)
    }
    own_blocked = {
        sid: sec for sid, sec in beat.strategy_blocked_sec.items() if sid in own_strategies
    }
    strategies_block = {
        # 具名，不是計數。他要知道的是「哪一支現在不會發提醒」，而 /healthz 依設計說
        # 不出這件事（它沒有憑證）。
        "blocked": [
            {"strategy_id": sid, "name": own_strategies[sid], "blocked_sec": round(sec, 1)}
            for sid, sec in sorted(own_blocked.items())
        ]
    }
    if settings.WORKER_ENABLED:
        if any(sec > settings.HEALTH_MAX_STRATEGY_BLOCKED_SEC for sec in own_blocked.values()):
            overall = _worse(overall, _FAIL)
        if any(gap > settings.HEALTH_MAX_SYMBOL_GAP_SEC for gap in own_bar_gaps.values()):
            overall = _worse(overall, _FAIL)
        elif own_blocked:
            # 還沒跨過門檻是一次重生失敗，不是停擺——但那正是停擺開始的樣子。
            overall = _worse(overall, _WARN)

    if settings.WORKER_ENABLED:
        if beat.consecutive_empty_polls >= settings.HEALTH_MAX_EMPTY_POLLS:
            overall = _worse(overall, _FAIL)
        # Also the caller's own: another account's stale symbol turning this
        # page amber would be an alarm about something they cannot see and
        # cannot fix.
        if any(gap > settings.HEALTH_MAX_SYMBOL_GAP_SEC for gap in own_gaps.values()):
            overall = _worse(overall, _FAIL)
        elif own_gaps:
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

    # HOW MANY ACCOUNTS THIS DEPLOYMENT HAS, because the owner should not have
    # to go and find out.
    #
    # Registration closes itself once there is an owner, but that only stops
    # NEW accounts -- it cannot remove one created while the door was still
    # open, and every 「your data is yours」 guarantee rests on there being
    # exactly one. Checking used to mean opening the database console and
    # running a SELECT, which CLAUDE.md is explicit about: never send this
    # reader somewhere else for a value the app already knows.
    account_count = db.query(User.id).count()
    accounts = {
        "count": account_count,
        "expected": 1,
        "status": _OK if account_count <= 1 else _WARN,
    }
    if account_count > 1:
        accounts["detail"] = (
            f"這個部署有 {account_count} 個帳號，但它是設計給一個人用的。"
            "多出來的帳號是在「註冊自動關閉」這個修補之前建立的——"
            "請確認每一個都是你自己的，不是的話要移除。"
        )

    # WHERE THE DATA LIVES, because the owner has no way to know from any other
    # screen. That value was typed into a hosting platform's form, possibly
    # weeks ago, possibly never -- and 「never」 lands on the default file
    # database, which looks identical to a working Postgres until the redeploy
    # that empties it.
    #
    # NEVER THE CONNECTION STRING. It carries the password and this block is
    # rendered on a page.
    url = (settings.DATABASE_URL or "").strip()
    if url.startswith("sqlite"):
        kind = "sqlite"
    elif url.startswith(("postgres://", "postgresql://", "postgresql+")):
        kind = "postgres"
    else:
        kind = "other"
    # Only a platform's disk is the ephemeral one. The same file on somebody's
    # own machine is exactly where they put it, and calling that temporary
    # would be telling them to fix something that is not broken.
    host = hosting.detect()
    on_a_platform = host is not hosting.GENERIC
    ephemeral = kind == "sqlite" and on_a_platform
    # **遷移沒跑成功，但這個部署沒有被鎖住。**
    #
    # scripts/start.py 在「已經有帳號的部署」上刻意不鎖：一次跑不動的遷移不該讓一份跑
    # 了三個月的部署所有提醒停擺。但「不鎖」不等於「不說」——而在這一段之前它就是不
    # 說：那個環境變數被設下去之後，整個 app 沒有任何一行讀它，理由只留在容器的 log
    # 裡，而那是他不會打開的地方。他會打開的是這一頁。
    #
    # 為什麼不讓 /healthz 跟著紅：schema 真的對不上的話，查詢會失敗、盯盤那一輪會停，
    # 而 worker 那一格本來就會紅、看門狗本來就會寄信——**後果已經有人抓了，這裡補的是
    # 原因**。反過來直接紅則會亂叫：遷移失敗也可能只是拿不到鎖，schema 其實好好的，而
    # 那種紅燈要等到下一次重啟才會消失。
    stale = (os.environ.get("DATABASE_MIGRATION_STALE") or "").strip()
    if stale:
        # 原因原樣帶出來。「資料庫有問題」不是一個他可以拿去做事的句子，而這一串正是
        # 他要貼給別人看、或貼進「問 AI」的那一段。
        detail = (
            "上一次啟動時資料庫遷移沒有跑完，所以資料庫的結構可能跟現在這一版程式對不上。"
            "提醒沒有因此停掉，但畫面上如果有東西怪怪的，多半是這件事。"
            f"原因：{stale}"
        )
    elif ephemeral:
        detail = (
            "資料存在容器裡的一個檔案，而這個平台每次重新部署都會換一個新的容器"
            "——帳號、策略、通知設定會一起不見，而且不會有任何提示。"
            "去開一個 Postgres（免費的例如 Neon、Supabase），把連線字串放進 DATABASE_URL。"
        )
    elif kind == "sqlite":
        detail = (
            "資料存在本機的一個檔案裡。在自己的機器上這沒有問題，"
            "但那是一個檔案——記得跟其他重要檔案一起備份，這個系統不會替你備份它。"
        )
    else:
        detail = "資料存在 Postgres 裡。"

    database = {
        "kind": kind,
        "ephemeral": ephemeral,
        "status": _WARN if (ephemeral or stale) else _OK,
        "detail": detail,
    }

    return {
        "overall": overall,
        "worker": worker,
        "market_data": market_data,
        "strategies": strategies_block,
        "notifications": notifications,
        "accounts": accounts,
        "database": database,
        # 「那一格要去哪裡填」，用這個部署實際所在平台的說法。設定引導照這個講；
        # 對 Fly.io 的使用者說「Render 後台」比含糊更糟——他會真的去找那一頁。
        "platform": {"name": host.name, "env_where": host.env_where},
        "assistant_available": _assistant_available(db, user.id),
        # behind 是 None 的時候代表**不知道**，前端不可以把它畫成「已經是最新」。
        "update": update,
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
    # 少了這一句，助手會看到 overall=fail 卻每一格都正常，然後開始猜。
    blind = "、".join(
        f"{row['name']}（已 {row['blocked_sec']:.0f} 秒叫不動子行程）"
        for row in status["strategies"]["blocked"]
    )
    stuck_bars = "、".join(
        f"{row['series']}（已 {row['gap_sec']:.0f} 秒抓不到 K 棒）" for row in market["stale_bars"]
    )
    return (
        f"這個部署目前的狀態：\n"
        f"- 整體：{status['overall']}\n"
        f"- 背景 worker 啟用：{worker['enabled']}；"
        f"最後一次循環：{worker['last_loop_age_sec']} 秒前；"
        f"最後一次成功抓行情：{worker['last_poll_age_sec']} 秒前\n"
        f"- 連續抓不到任何價格的次數：{market['consecutive_empty_polls']}\n"
        f"- 抓不到報價的代號：{stale or '無'}\n"
        f"- 叫不動子行程的策略：{blind or '無'}\n"
        f"- 抓不到 K 棒的：{stuck_bars or '無'}\n"
        f"- 通知功能啟用：{notifications['enabled']}\n"
        f"- 最近 {notifications['window_hours']} 小時的通知："
        f"已送出 {notifications['sent']}、還在重試 {notifications['retrying']}、"
        f"等靜音 {notifications['deferred']}、已放棄 {notifications['given_up']}、"
        f"沒送到任何管道 {notifications['reached_nobody']}\n"
        f"- 資料庫：{status['database']['detail']}\n"
        f"- 這個部署在：{status['platform']['name']}"
        f"（環境變數在：{status['platform']['env_where']}）\n"
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
    result = get_ai_provider(ai_settings.resolve(db, user.id)).ask(
        question, system=_ASSISTANT_PROMPT
    )
    return AssistResult(ok=result.ok, reply=result.reply, error=result.error)


@router.get("/updates")
def system_updates(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    """從你這一版到最新之間，改了什麼。

    **刻意不塞進 /status。** 那一頁會被輪詢，而這裡每次都是一次對 GitHub 的呼叫；
    塞在一起的話，一個開著的分頁就能把沒登入的額度（每小時 60 次）用完，然後真的需
    要知道有沒有新版的時候問不到。

    空清單不代表「沒有更新」——分岔了、問不到，也都是空的。**為什麼是空的**由
    /status 的 `update` 那一格回答（它分得出落後和分岔），這裡只負責列清單。
    """
    running = build_info.commit()
    return {
        "running": running,
        "changes": update_check.changes_since(running) if running else [],
    }
