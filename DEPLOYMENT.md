# 部署指南（以 Render ＋ Neon ＋ Vercel 的免費方案為例）

全部走免費方案，不需要信用卡。以下每一步需要你自己動手（帳號註冊、環境變數填寫），
沒有人能幫你操作瀏覽器登入這些平台。

**這是一條走得通的路，不是唯一的一條。** 這個 app 需要的是三樣東西，不是三個品牌：

| 要的東西 | 這份文件用的例子 | 換成別的可以嗎 |
| --- | --- | --- |
| 一個能跑 Docker 容器的地方（後端） | Render | 可以：Railway、Fly.io、Koyeb、Heroku、或你自己的機器。映像檔是 `backend/Dockerfile` |
| 一個 Postgres | Neon | 可以：Supabase、任何付費方案、自架的都行 |
| 一個放靜態網站的地方（前端） | Vercel | 可以：Cloudflare Pages、Netlify、任何 CDN |

底下的步驟寫的是 Render／Neon／Vercel 的選單名稱，因為那是有截圖式指路的唯一辦法。
換平台的話，每一步問的東西是一樣的（「把這個值放進環境變數」），只是那一頁在你的
平台上可能叫 Variables、Config Vars 或 Secrets。**設定頁自己會照你實際用的平台講話**——
它認得出 Render、Railway、Fly.io、Heroku、Koyeb，認不出來就講通用的說法。

換平台唯一要注意的技術限制：這個後端**只能跑一個行程**（`--workers 1`），因為背景盯盤
的迴圈是行程內的單例，開兩份會把每一個訊號通知兩次。

## 順序

1. 資料庫（本文用 Neon）
2. 後端 API + 背景 worker（本文用 Render）
3. 前端網頁（本文用 Vercel）
4. 保活（本文用 UptimeRobot，讓免費方案的背景 worker 不休眠）
5. 回頭把兩邊的網址互相填好

---

## 1. 建立 Postgres 資料庫（本文用 Neon 免費方案）

1. 前往 <https://console.neon.tech> 註冊（可以用 GitHub 帳號登入）。
2. 建立一個新專案（Project），資料庫名稱隨意，例如 `trading_app`。
3. 進到專案後找到 **Connection string**，格式類似：
   ```
   postgresql://user:password@ep-xxxx.region.aws.neon.tech/trading_app?sslmode=require
   ```
4. **重要**：我們用的是 `psycopg2`（同步驅動），Neon 預設給的連線字串開頭通常是 `postgresql://`，這樣就對了（不需要改成 `postgresql+psycopg2://`，SQLAlchemy 預設就會用 psycopg2）。把這串網址存起來，等一下要貼到 Render。

Neon 免費方案的資料庫在閒置一段時間後會自動暫停，下次連線時會自動喚醒（有一點點延遲），這對我們的用途沒問題。

---

## 2. 部署後端（本文用 Render）

### 2a. 把專案推上 GitHub（Render 需要接 GitHub repo）

如果你還沒有這個專案的 GitHub repo，先建立一個（可以是 private）：

1. 前往 <https://github.com/new> 建立一個新 repo（例如 `stock-trading-app`）。
2. 依照 GitHub 顯示的指令，把本地這個資料夾推上去（我可以幫你執行 git 指令，但需要你先確認 repo 網址）。

### 2b. 用 Blueprint 一鍵部署

專案根目錄已經有 `render.yaml`，可以用 Render 的 Blueprint 功能一次建立好服務：

1. 前往 <https://dashboard.render.com/blueprints> 註冊/登入（可以用 GitHub 帳號）。
2. 點 **New Blueprint Instance**，選擇你剛推上去的 repo。
3. Render 會讀取 `render.yaml` 並列出要建立的服務（`trading-app-backend`），確認後建立。
4. 建立過程中，Render 會要求你手動填入標記 `sync: false` 的環境變數：
   - `DATABASE_URL`：貼上一開始從 Neon 拿到的連線字串
   - `SECRET_ENCRYPTION_KEY`：需要先在本機產生一組真正的 Fernet 金鑰，執行：
     ```
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
     把印出來的字串貼進去。**貼上去之前，先把這串字存進你的密碼管理器**——這把鑰匙是唯一的，
     弄丟了就再也解不開已經存起來的券商金鑰和通知帳密，連在網頁上刪掉重設都做不到。
     詳見第 7 節〈備份〉。
   - `CORS_ORIGINS`：先隨便填 `http://localhost:5173`，等第 3 步 Vercel 部署完拿到正式網址後回來改
5. 部署完成後，Render 會給你一個網址，例如 `https://trading-app-backend-xxxx.onrender.com`。記下來，第 3 步會用到。
6. 打開 `https://<你的網址>/healthz`，最外層看到 `"status": "ok"`（HTTP 200）就代表後端上線成功。剛部署完、背景 worker 還沒跑完第一輪時，`worker` 和 `market_data` 會顯示 `starting`，這是正常的。

---

## 3. 部署前端（本文用 Vercel）

1. 前往 <https://vercel.com/new> 註冊/登入（用 GitHub 帳號）。
2. 選擇同一個 GitHub repo，Root Directory 設定為 `frontend`。
3. Vercel 會自動偵測 Vite 專案，Build Command / Output Directory 用預設值就好。
4. 在 **Environment Variables** 加入：
   - `VITE_API_BASE_URL` = 你的 Render 後端網址（例如 `https://trading-app-backend-xxxx.onrender.com`）
   - `VITE_WS_URL` = 同一個網址但開頭改成 `wss://`（例如 `wss://trading-app-backend-xxxx.onrender.com`）
5. 部署完成後會拿到一個網址，例如 `https://your-app.vercel.app`。

### 回頭更新後端的 CORS_ORIGINS

1. 回到 Render 的服務設定，把 `CORS_ORIGINS` 改成你剛拿到的 Vercel 網址（例如 `https://your-app.vercel.app`）。
2. 存檔後 Render 會自動重新部署一次。

---

## 4. 讓背景 worker 不休眠（本文用 UptimeRobot）

Render 免費方案閒置 15 分鐘會自動休眠（休眠時背景策略監控不會執行），用免費的外部監控服務每 5 分鐘 ping 一次來保持喚醒：

1. 前往 <https://uptimerobot.com> 免費註冊。
2. 新增一個 **HTTP(s) Monitor**：
   - URL：`https://<你的 Render 後端網址>/healthz`
   - Monitoring Interval：5 分鐘
3. 存檔後就會開始每 5 分鐘自動 ping 一次，讓服務保持清醒。
4. **記得在 monitor 的 Alert Contacts 加上你的 Email**，否則就算後端壞掉也不會通知你。

### `/healthz` 到底檢查什麼

這個網址同時是「保持喚醒」和「唯一的線上監控」。它會實際去測三件事，任何一項失敗就回 **HTTP 503**，UptimeRobot 收到 503 才會寄信給你：

| 檢查項目 | 失敗代表 |
| --- | --- |
| `database` | 資料庫連不上（Neon 掛掉、連線字串失效） |
| `worker` | 背景 worker 沒在跑迴圈了（卡死，或根本沒起來） |
| `market_data` | 迴圈還在轉，但已經很久沒有一輪報價成功跑完 |

回應只有檢查項目名稱和秒數，不會帶出連線字串、金鑰或持股標的，所以公開被 ping 是安全的。

### 怎麼知道線上跑的是哪一版

`/healthz` 的回應裡有一段 `version`：

```json
{ "status": "ok", "version": { "commit": "41298da", "started_at": "2026-08-22T09:41:00Z" }, ... }
```

- `commit`：線上這個版本是哪一個 commit。跟 GitHub 上最新的那一個比對，一樣就代表部署到了。
- `started_at`：這個行程是什麼時候啟動的。部署成功會重新啟動，所以這個時間會往前跳。

**為什麼需要這個**：後端跑的是舊版時，上面每一項檢查都會是綠的——舊版並沒有生病，
它只是舊的。沒有這一段，「我推上去的東西到底上線了沒」就只能靠記得有沒有按過按鈕。

CI 的部署步驟自己會看這個欄位：呼叫部署 hook 之後，它會一直問線上「你是哪一個 commit」，
等到答案變成剛推上去的那一個才算成功；等不到就把那次 CI 變成紅燈並寄信給你。
（要有這個確認，`HEALTH_URL` 這個 repository variable 要設好，就是 UptimeRobot 那一節用的同一個網址。）

`commit` 是從環境變數讀的，`APP_GIT_COMMIT` 是這個 app 自己的名字；
Render（`RENDER_GIT_COMMIT`）、Heroku／Dokku（`SOURCE_VERSION`）、Railway、Koyeb 這些
平台自己會塞的名字也都認得，所以多數情況下你什麼都不用設定。
自己 build 映像檔的話：`docker build --build-arg APP_GIT_COMMIT=$(git rev-parse HEAD) ...`。
兩邊都沒有時，這一格是 `null`——**寧可說不知道，也不會編一個版本號給你看**。

另外注意：`render.yaml` 的 `healthCheckPath` 也指向 `/healthz`，所以持續 503 時 Render 也會判定服務不健康。worker 卡死的情況下重啟本來就是正確處置，但如果之後不想要這個連動，把 `healthCheckPath` 改成別的路徑即可。

---

## 5. 建立你的第一個帳號

正式環境的 `ALLOW_REGISTRATION` 預設是關閉的（安全考量，避免任何人都能註冊）。要建立你自己的登入帳號，需要透過後端的腳本執行：

由於 Render 免費方案沒有互動式 shell，最簡單的方式是**先臨時把 `ALLOW_REGISTRATION` 改成 `true`**：

1. 到 Render 後端服務的環境變數，把 `ALLOW_REGISTRATION` 改成 `true`，存檔（會自動重新部署）。
2. 到你的 Vercel 前端網址，目前登入頁沒有註冊表單的 UI，改用瀏覽器打 API：
   ```
   curl -X POST https://<你的Render網址>/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{"email":"你的信箱","password":"你的密碼"}'
   ```
3. 註冊完成後，把 `ALLOW_REGISTRATION` 改回 `false`（重要：避免任何人都能自己註冊帳號）。
4. 之後就用這組帳密在 Vercel 網址上登入。

---

## 6. 啟用瀏覽器推播通知（選用）

想在「通知」頁用「瀏覽器推播」這個管道，需要先在 Render 後端設定一組 VAPID 金鑰：

1. 在本機執行：
   ```
   python backend/scripts/generate_vapid_keys.py
   ```
   會印出 `VAPID_PUBLIC_KEY` 跟 `VAPID_PRIVATE_KEY` 兩個值。
2. 到 Render 後端服務的環境變數，新增這兩個欄位並貼上對應的值，`VAPID_SUBJECT` 保持預設值即可（或改成你自己的 `mailto:` 信箱）。
3. 存檔後 Render 會自動重新部署一次；之後就能在「通知」頁選「瀏覽器推播」建立管道了。

不設定也沒關係——LINE/Telegram/Email 三種管道完全不受影響，只是瀏覽器推播那個選項在測試時會顯示「VAPID_PRIVATE_KEY is not configured」。

---

## 7. 備份（金鑰弄丟＝資料救不回來，請務必做）

這一節不是選用的。前面每一步都只是「把服務架起來」，這一步是「服務壞掉的時候還救得回來」。

### 7a. 一定要另外保存的金鑰

| 環境變數 | 弄丟的話會怎樣 | 能重新產生嗎 |
| --- | --- | --- |
| **`SECRET_ENCRYPTION_KEY`** | **最嚴重。** 已經存進去的通知管道（Telegram bot token、LINE token、Email 密碼）和券商 API 金鑰，會全部永久無法解密。而且連「在網頁上刪掉重設」都做不到——刪除和修改這兩個動作都得先把那一欄讀出來、確認是不是你的資料，一讀就會失敗。剩下的辦法只有請人直接進資料庫，把那兩張表整批刪掉重來。 | **不行。** 這把鑰匙是唯一的，沒有備用鑰匙、沒有客服可以幫你解。 |
| `DATABASE_URL` | 後端連不到資料庫 | 可以，回你的資料庫服務主控台重新複製一次 |
| `JWT_SECRET` | 所有人被登出 | 可以，換一組新的、重新登入就好 |
| `TV_WEBHOOK_SECRET` | TradingView 送來的訊號會被擋掉 | 可以，但換完要回 TradingView 把每個警報訊息裡的 `secret` 也一起改掉 |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | 瀏覽器推播失效 | 可以，但重新產生後每台裝置都要回「通知」頁重新訂閱一次 |

**要存在哪裡**

1. 用密碼管理器（Bitwarden、1Password、Apple 密碼、Google 密碼管理工具都可以）開一筆安全筆記，命名成類似「Stock trading app 正式環境金鑰」，把上表所有的值貼進去，並註明是哪一天存的。
2. **至少要兩份，而且不能在同一個地方。** 例如：密碼管理器一份 ＋ 印出來放家裡抽屜一份。
3. **不要放的地方**：GitHub（就算是 private repo 也不行）、用 LINE/Messenger 傳給自己、桌面上的 `.txt`、沒有加密的雲端硬碟。

> **不要把 Render 當成你的備份。** 服務被刪掉、或帳號出狀況的時候，上面的環境變數會跟著一起消失。正確的順序是**先存進密碼管理器，再貼到 Render**，不要等貼完才想到要備份。

### 7b. 資料庫備份（Neon）

**先說 Neon 自己的功能**：Neon 有內建的「時間點還原」（Point-in-time restore），但免費方案能回溯的時間很短（以小時計，Neon 也可能隨時調整）。它能救「剛剛不小心刪錯一筆」，**不能**當成長期備份。所以還是要自己定期匯出一份檔案存起來。

**第一次要先裝工具**（只裝工具程式，不用在自己電腦跑資料庫）：

- Windows：到 <https://www.postgresql.org/download/windows/> 下載安裝檔，安裝過程中元件只勾 **Command Line Tools** 就好。
- macOS：打開「終端機」執行 `brew install libpq`。

**備份（匯出）**

打開終端機（Windows 是「命令提示字元」或 PowerShell），貼上這一行後按 Enter：

```
pg_dump "postgresql://user:password@ep-xxxx.region.aws.neon.tech/trading_app?sslmode=require" -Fc -f trading_app_2026-08-18.dump
```

- 引號裡面那一整串就是 `DATABASE_URL`，直接從密碼管理器貼過來，**整串都要包在雙引號裡**（裡面有 `?` 和 `&`，不加引號會被系統誤解）。
- `-Fc` 是壓縮格式，還原時要搭配 `pg_restore` 使用。
- `-f` 後面是要存成的檔名，**建議把日期寫進檔名**，之後才分得出哪份是哪份。
- 跑完之後看一下檔案：只要不是 0 KB，就代表成功了。中途沒有任何訊息是正常的。

**還原（把備份倒回去）**

```
pg_restore --clean --if-exists --no-owner -d "postgresql://...要還原到的連線字串..." trading_app_2026-08-18.dump
```

- `--clean --if-exists`：先把同名的表刪掉再重建，所以就算目標資料庫裡已經有東西也能還原。
- `--no-owner`：不要沿用原本的擁有者名稱——換一個 Neon 專案之後使用者名稱會不一樣，不加這個會報錯。

> **還原時最容易踩的坑**：還原回來的通知管道和券商金鑰，仍然是用**當初那把 `SECRET_ENCRYPTION_KEY`** 加密的。所以還原資料庫的同時，Render 上的 `SECRET_ENCRYPTION_KEY` 必須設回備份當時的那一把，否則資料明明還在，卻永遠打不開。這就是 7a 為什麼要你把那把鑰匙另外存好的原因。

### 7c. 多久做一次

| 什麼時候 | 做什麼 |
| --- | --- |
| **每個月一次**（建議在手機行事曆設一個每月重複的提醒） | 跑一次 `pg_dump`，存成當月的檔案 |
| **每次改金鑰、或新增了券商／通知管道之後** | 立刻更新密碼管理器裡那筆筆記，並補跑一次 `pg_dump` |
| **重大變更前**（換方案、搬資料庫、大改版） | 動手之前一定先備份一份 |

- **保留幾份**：最近三個月各留一份就夠了，更舊的可以刪。檔案很小，通常只有幾 MB。
- **備份要驗證過才算數**：建議一年至少一次，在 Neon 上開一個新的空專案，把最近一份 dump 還原上去，確認真的還原得起來，再把那個測試專案刪掉。沒有驗證過的備份，不能算是備份。

---

## 8. 上線壞掉的時候：怎麼退回去

寫在這裡是因為需要它的那一刻，沒有人有心情從頭想一遍。

### 先分清楚是哪一種壞

在 Render 的 Logs 看啟動訊息，兩種壞法的處理方式完全不同：

| 症狀 | 多半是 |
| --- | --- |
| 服務起不來、log 停在 `alembic upgrade head` | 遷移本身失敗 |
| 服務起得來但功能不對、log 有 Python traceback | 程式碼的問題 |
| 前幾秒 502 之後就正常 | Neon 冷啟動，不是故障，等一下就好 |

### 情況 A：程式碼壞了，但資料庫結構沒變

最單純。Render → 該服務 → **Events**，找到上一個成功的 deploy → **Rollback**。
幾分鐘內回到舊版，資料完全不動。

### 情況 B：程式碼壞了，而且這一版帶了遷移

**不能直接 Rollback。** 容器每次啟動都會跑 `alembic upgrade head`，資料庫已經在新
結構上，舊程式碼配新結構通常直接 500——你會從一個壞掉換到另一個壞掉。

順序是：**先把資料庫降回去，再回舊版程式**。免費方案沒有 shell，所以降級要從你自己的
電腦跑：

```bash
cd backend
# DATABASE_URL 用 Neon 的連線字串（Render 環境變數裡那一條）
DATABASE_URL="postgresql://..." python -m alembic downgrade -1
```

`-1` 是退一個版本。要退多個就多跑幾次，或用 `downgrade <revision>` 指定。
每一支遷移都寫了 `downgrade()`，所以這是可行的——但**先做備份**（第 7 節），
因為 downgrade 會丟掉那個欄位裡的資料。

降完之後再到 Render 做 Rollback。

### 情況 C：資料本身壞了（不是程式）

程式沒問題，是資料被寫壞了。Rollback 沒有用。

1. Neon 的 **Restore**（免費方案只保留幾小時，過了就沒有）
2. 或用第 7 節的加密備份還原：`python scripts/inspect_backup.py 檔案` 先看內容

### 事後

Render 的 Events 只說「哪一次部署」，不說「那是哪一版程式」。要對得起來，
到 GitHub 看 commit 時間，跟 deploy 時間對照。

---

## 檢查清單

- [ ] Postgres 資料庫建立完成，拿到連線字串
- [ ] GitHub repo 建立並推送完成
- [ ] 後端部署完成，`/healthz` 回應正常
- [ ] 前端部署完成，能打開登入頁
- [ ] 後端 `CORS_ORIGINS` 已更新成前端網址
- [ ] 保活監控已設定（免費方案才需要）
- [ ] 已建立自己的登入帳號，且 `ALLOW_REGISTRATION` 已改回 `false`
- [ ] （選用）已設定 `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`，瀏覽器推播通知可以用
- [ ] **`SECRET_ENCRYPTION_KEY` 等金鑰已存進密碼管理器，而且存了兩份不同地方**（見第 7 節）
- [ ] 已經成功跑過一次 `pg_dump`，手上有一份資料庫備份檔
- [ ] 手機行事曆已設好每月備份提醒
