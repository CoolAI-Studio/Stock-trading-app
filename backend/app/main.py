import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import NotModifiedResponse

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

# 後端永遠擁有的前綴。**兩個地方共用這一份**：底下的靜態檔 fallback 用它決定「這條
# 路不是我的」，設定模式那個鎖用它的反面決定「這條路是畫面，要放行」。分成兩份的話，
# 它們會各自漂移，而漂移的那一天沒有東西會變紅。
_BACKEND_OWNS = ("/api", "/healthz", "/ws", "/docs", "/openapi.json", "/redoc")


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

    # **畫面要通。** 設定頁是前端的一頁，而前端現在跟 API 在同一個服務上（#53）——
    # 擋掉它就等於擋掉那個唯一能告訴他「你還缺什麼、按這顆按鈕產生」的東西，而那正是
    # 這個時間點上唯一有用的畫面。他手上只有一個網址，打開是一行 JSON 的話，對一個不
    # 是工程師的人來說流程就到此為止。
    #
    # 這是實地量出來的，不是想出來的：first-deploy 那個 job 用全空的設定跑真的容器，
    # `GET /` 回 503。前端還在 Vercel 的時候 `/` 根本不會經過這個中介層，所以搬進來
    # 之前這個鎖是對的——它只是沒有跟著搬。
    #
    # 判準用 _BACKEND_OWNS 的反面，不是另外列一份「設定模式可以看的靜態檔」白名單：
    # 那種白名單漏掉一個雜湊過的檔名就是一片白畫面，而白畫面沒有訊息也沒有狀態碼。
    # 反過來寫的話，會被擋的永遠只有後端自己那幾個前綴。
    if not any(path.startswith(prefix) for prefix in _BACKEND_OWNS):
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


# --- 前端 --------------------------------------------------------------------
#
# **後端直接供應前端，所以只要部署一次。**
#
# 原本要部署兩次：後端（Render／Railway／Fly.io／自己的機器都行）加前端（只給了
# Vercel）。那個不對稱有兩個後果——引導頁對後端給三個選擇、對前端只給一個；而更新的
# 路徑被綁在 Vercel 上（sync-from-upstream.yml 只在「前端是一份 GitHub 複製品而且
# Actions 開著」的時候有用）。
#
# 後端供應前端之後，每一條**後端**的路都自動涵蓋前端，而那些路本來就是通用的。
#
# 這不是拿掉 Vercel，是拿掉「必須再部署一次」：想把前端另外放的人照樣可以，設
# VITE_API_BASE_URL 指向他的後端就好。少掉的是要求，不是選擇。
#
# **掛在最後面。** 它會吃掉所有沒被上面接走的路徑，所以順序就是正確性：排在 API 前
# 面的話，`/api/...` 會拿到 index.html，而前端在等 JSON——錯誤訊息會是「Unexpected
# token '<'」，跟真正的原因差了十萬八千里。
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

# 這幾個前綴永遠是後端的，就算 dist 存在。
#
# 列出來而不是只靠「排在前面」：排序是一個沒有東西守著的約定，而它壞掉的方式是靜默
# 的。


# Vite 把每一支 bundle 的內容雜湊寫進檔名（index-CNbqHiLX.js），所以那個網址的內容
# 永遠不會變——改一個字檔名就變了。這些用力快取是安全的，而且是必要的：不快取的話他
# 每次打開 app 都要用行動網路重新下載 660 KB。
_HASHED_ASSETS_PREFIX = "/assets/"
_KEEP_FOR_A_YEAR = "public, max-age=31536000, immutable"

# 其他每一個檔案的網址都是固定的（index.html、sw.js、manifest、圖示），所以它的新版
# 和舊版是同一個網址。
#
# **`no-cache` 不是「不要存」，是「存可以，但用之前要問一次」。** 少了這個標頭，
# 瀏覽器會用 RFC 9111 §4.2.2 的啟發式快取：拿 Last-Modified 到現在的十分之一當新鮮
# 期。而 Last-Modified 就是映像檔裡那個檔案的時間，也就是上一次建置的時間——所以
# **我們愈久沒改東西，他卡在舊版的時間就愈長**（建置後 60 天 → 6 天不問伺服器）。
#
# 而卡住的後果不只是看到舊畫面：系統狀態頁會拿那份被快取住的 bundle 裡的
# FRONTEND_COMMIT 去比對，然後告訴他「你看到的這個畫面是舊的，去重新部署一次」——一
# 句真話配一個沒有用的辦法，因為伺服器上早就是新的了。
_ASK_EVERY_TIME = "no-cache"


def _static(candidate: Path, request: Request) -> Response:
    """一個靜態檔，附上它該有的快取規則，而且答得出 304。

    304 讓「每次問一次」真的便宜（幾十個位元組，不是整個檔案）。
    `FileResponse` 自己不做這件事——那段條件式判斷在 Starlette 裡是 `StaticFiles`
    的，而這裡是一個普通的路由函式，所以要自己接。
    """
    cache = (
        _KEEP_FOR_A_YEAR if request.url.path.startswith(_HASHED_ASSETS_PREFIX) else _ASK_EVERY_TIME
    )
    # **`stat_result` 要自己給。** 不給的話 `FileResponse` 會等到真的開始送的時候才
    # 去 stat，而 ETag 是那時候才算出來的——所以在這裡讀 `headers["etag"]` 會是空的，
    # 底下那個比對就永遠不成立，304 從來不會發生（第一版就是這樣，測試抓到了）。
    response = FileResponse(
        candidate, stat_result=candidate.stat(), headers={"Cache-Control": cache}
    )
    etag = response.headers.get("etag")
    # 只看 ETag，不看 If-Modified-Since。我們每一個回應都帶 ETag，而瀏覽器手上有
    # ETag 的時候一定會送 If-None-Match，所以這一條涵蓋得到每一個真實情況。
    if etag and request.headers.get("if-none-match") == etag:
        # 用 Starlette 自己那一個，不要自己組：304 只准帶特定幾個標頭（RFC 9110
        # §15.4.5），而它知道是哪幾個。
        return NotModifiedResponse(response.headers)
    return response


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str, request: Request) -> Response:
    """真的存在的檔案照原樣送；其他都回 index.html。

    後者是單頁應用必須的：路由在瀏覽器裡，不在伺服器上。使用者按 F5 重新整理
    `/strategies`，伺服器上沒有那個檔案——回 404 的話他看到的是一個壞掉的網站，而他
    什麼都沒做錯。

    **沒有 dist 也要能起來。** 開發環境沒建過前端，一個只建後端的映像檔也沒有。一個
    因為找不到靜態檔而起不來的 API，是把「畫面沒了」升級成「提醒沒了」——而這個 app
    的鐵律是警告不能停擺。
    """
    if any(("/" + full_path).startswith(prefix) for prefix in _BACKEND_OWNS):
        # 走到這裡代表上面每一個路由都沒接——也就是那個 API 路徑不存在。回 JSON 的
        # 404，不是 app 的外殼：回 HTML 的話，一個打錯的網址會讓前端以為請求成功
        # 了，然後在解析的時候炸掉。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not FRONTEND_DIST.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="這一份部署沒有內建前端。前端另外部署的話，把它指向這個後端的網址。",
        )

    # `resolve()` 之後確認它還在 dist 底下：`full_path` 是使用者給的，而 `..` 是一
    # 個一路走到檔案系統根目錄的門。
    candidate = (FRONTEND_DIST / full_path).resolve()
    if candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
        return _static(candidate, request)

    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return _static(index, request)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
