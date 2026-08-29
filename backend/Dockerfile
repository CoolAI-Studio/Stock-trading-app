# Platform-agnostic -- builds and runs the same way on Render, Fly.io, or
# any other host that can run a container and inject a PORT env var.

# --- 前端 ---------------------------------------------------------------------
#
# **這個映像檔同時供應前端，所以只要部署一次。**
#
# 原本要部署兩次：後端（Render／Railway／Fly.io／自己的機器都行）加前端（只給了
# Vercel）。那個不對稱讓更新的路徑被綁在一家廠商上——而每一條後端的路本來就是通用
# 的，所以把前端放進來，通用性是免費得到的。
#
# 這不是拿掉 Vercel，是拿掉「必須再部署一次」：想把前端另外放的人照樣可以，設
# VITE_API_BASE_URL 指向他的後端。少掉的是要求，不是選擇。
#
# **build context 是 repo 根目錄**，不是 ./backend——這一段要 COPY frontend/，下一段
# 要 COPY backend/。render.yaml 的 dockerContext、docker-compose 的 context、CI 的
# `docker build -f backend/Dockerfile .` 三個都是 `.`，而它們之間沒有任何連結，所以
# 由 tests/test_one_deployment_serves_both_halves.py 綁著。
#
# 排除清單也跟著搬了：Docker 只讀 context 根目錄的 .dockerignore，所以那個檔案在
# repo 根目錄，不在 backend/。
FROM node:22-slim AS frontend

WORKDIR /build
# 先複製相依清單再 npm ci：這兩個檔案不變的時候，Docker 會重用整層快取，而
# node_modules 是這個 build 裡最慢的一步。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# 前端要說得出自己是哪一版（#53）。這個值由建置環境給，沒給就是「不知道」——而
# 「不知道」不可以被顯示成「已經是最新」。
ARG APP_GIT_COMMIT=""
# 名字要跟 vite.config.ts 讀的那一個一樣（`process.env.APP_GIT_COMMIT`）。
#
# 這裡本來寫的是 VITE_APP_COMMIT，看起來很合理——Vite 的慣例就是 VITE_ 開頭——但
# vite.config.ts 的 `define` 走的不是 import.meta.env 那條路，它直接讀 process.env。
# 所以名字對不起來，映像檔裡的前端版本永遠是空的，徽章永遠顯示「不知道」。
#
# 而這件事不會讓任何東西變紅：建置成功、bundle 正常、畫面能用。它只是安靜地把整個
# 徽章變成廢的——而那個徽章存在的理由，就是讓「沒跟上更新」看得見。
ENV APP_GIT_COMMIT=${APP_GIT_COMMIT}

# `npx vite build`，不是 `npm run build`（那是 `tsc -b && vite build`）。
#
# 這裡**只負責打包，不負責驗證**。型別檢查是 CI 那個 frontend job 的事，而 deploy job
# 要三個 job 全綠才會跑——所以任何走到這一步的 commit，型別早就檢查過了。在這裡再跑一
# 次 tsc 換不到任何安全性：它只可能在一個 CI 已經放行的 commit 上失敗。
#
# 換到的是記憶體和時間。tsc 是這一段裡最吃記憶體的一步（它要把整個專案連測試檔一起載
# 進來），而免費方案的建置環境就是靠這種地方決定建不建得完。產物完全一樣：tsc 不產生
# 任何 bundle 內容，vite 用 esbuild 自己剝掉型別。
RUN npx vite build

# --- 後端 ---------------------------------------------------------------------
FROM python:3.13-slim

WORKDIR /app

# bcrypt<4.1 is pinned in requirements.txt for passlib compat (see
# app/core/security.py) -- no extra system packages needed for it or for
# cryptography/psycopg2-binary on this base image.
# requirements.lock, not requirements.txt: the ranges in requirements.txt say
# what this app depends on, and the lock says what it was last tested with.
# Building from the ranges meant every Manual Deploy pulled whatever the
# upstreams had published that day, months after the version anyone had run --
# and one breaking release stops this container starting, which with the CMD
# below is the whole service. CI installs the same file, so the tests run
# against the build that ships.
COPY backend/requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY backend/ .

# 建好的前端。app/main.py 從這裡供應它，而**找不到也要能起來**——一個因為缺靜態檔
# 而起不來的 API，是把「畫面沒了」升級成「提醒沒了」。
COPY --from=frontend /build/dist /frontend/dist

ENV PYTHONUNBUFFERED=1

# Which build is this? /healthz reports it, so 「did my deploy actually land?」
# has an answer instead of a guess -- an out-of-date backend passes every
# health check, because an old build is not a sick one.
#
# Optional, and nothing breaks without it: hosts that already inject their own
# (RENDER_GIT_COMMIT, SOURCE_VERSION, RAILWAY_GIT_COMMIT_SHA, ...) are read at
# runtime instead, and a build with neither reports no commit rather than a
# wrong one. For a hand-built image:
#     docker build --build-arg APP_GIT_COMMIT=$(git rev-parse HEAD) ...
ARG APP_GIT_COMMIT=""
ENV APP_GIT_COMMIT=${APP_GIT_COMMIT}

EXPOSE 8000

# Migrations still run on every boot (idempotent -- a no-op once already at
# head), but through a script rather than `alembic ... && uvicorn ...`.
#
# THE `&&` WAS THE BUG. A DATABASE_URL with one character wrong makes alembic
# exit non-zero, so uvicorn never ran and no port was ever bound: the deployer
# got a dead URL. And DATABASE_URL is the one value on that form the app cannot
# generate -- it has to be copied from somebody else's console -- so it is the
# single most likely field to be wrong.
#
# scripts/start.py reports a failed migration instead of dying on it, then
# starts the server anyway: the setup page exists precisely to explain this
# situation, and it can only do that from a process that is alive.
#
# --workers 1 is set inside that script: the background market-data worker and
# the in-memory WS ticket store are process-local singletons, and a second
# worker process would run its own polling loop and duplicate every signal.
CMD ["python", "scripts/start.py"]
