# 部署指南（Render 免費方案 + Neon + Vercel + UptimeRobot）

全部走免費方案，不需要信用卡。以下每一步需要你自己動手（帳號註冊、環境變數填寫），我沒辦法幫你操作瀏覽器登入這些平台。

## 順序

1. Neon（資料庫）
2. Render（後端 API + 背景 worker）
3. Vercel（前端網頁）
4. UptimeRobot（保活，讓 Render 免費方案的背景 worker 不休眠）
5. 回頭把兩邊的網址互相填好

---

## 1. Neon — 建立免費 Postgres 資料庫

1. 前往 <https://console.neon.tech> 註冊（可以用 GitHub 帳號登入）。
2. 建立一個新專案（Project），資料庫名稱隨意，例如 `trading_app`。
3. 進到專案後找到 **Connection string**，格式類似：
   ```
   postgresql://user:password@ep-xxxx.region.aws.neon.tech/trading_app?sslmode=require
   ```
4. **重要**：我們用的是 `psycopg2`（同步驅動），Neon 預設給的連線字串開頭通常是 `postgresql://`，這樣就對了（不需要改成 `postgresql+psycopg2://`，SQLAlchemy 預設就會用 psycopg2）。把這串網址存起來，等一下要貼到 Render。

Neon 免費方案的資料庫在閒置一段時間後會自動暫停，下次連線時會自動喚醒（有一點點延遲），這對我們的用途沒問題。

---

## 2. Render — 部署後端

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
     把印出來的字串貼進去
   - `CORS_ORIGINS`：先隨便填 `http://localhost:5173`，等第 3 步 Vercel 部署完拿到正式網址後回來改
5. 部署完成後，Render 會給你一個網址，例如 `https://trading-app-backend-xxxx.onrender.com`。記下來，第 3 步會用到。
6. 打開 `https://<你的網址>/healthz`，看到 `{"status":"ok"}` 就代表後端上線成功。

---

## 3. Vercel — 部署前端

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

## 4. UptimeRobot — 讓背景 worker 不休眠

Render 免費方案閒置 15 分鐘會自動休眠（休眠時背景策略監控不會執行），用免費的外部監控服務每 5 分鐘 ping 一次來保持喚醒：

1. 前往 <https://uptimerobot.com> 免費註冊。
2. 新增一個 **HTTP(s) Monitor**：
   - URL：`https://<你的 Render 後端網址>/healthz`
   - Monitoring Interval：5 分鐘
3. 存檔後就會開始每 5 分鐘自動 ping 一次，讓服務保持清醒。

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

## 檢查清單

- [ ] Neon 資料庫建立完成，拿到連線字串
- [ ] GitHub repo 建立並推送完成
- [ ] Render Blueprint 部署完成，`/healthz` 回應正常
- [ ] Vercel 部署完成，能打開登入頁
- [ ] 後端 `CORS_ORIGINS` 已更新成 Vercel 網址
- [ ] UptimeRobot 監控已設定
- [ ] 已建立自己的登入帳號，且 `ALLOW_REGISTRATION` 已改回 `false`
- [ ] （選用）已設定 `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`，瀏覽器推播通知可以用
