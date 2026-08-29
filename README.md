# Stock Trading App

個人股票交易儀表板：網頁化的策略監控、手動確認下單、TradingView Webhook 訊號、即時報價推播、
LINE/Telegram/Email 通知。後端 FastAPI + SQLAlchemy，前端 Vite + React + TypeScript。

> **只想用、不想讀程式碼？**
> [**➜ 開始使用（圖解引導）**](https://coolai-studio.github.io/Stock-trading-app/)
> —— 一頁講完「放自己電腦還是放雲端」「雲端要用哪一家」，然後才回到這裡。
> 步驟跟下面完全一樣，只是有圖。

## 想要自己的一份嗎？一鍵部署

點下面這顆按鈕，用你自己的帳號部署一份完全獨立的系統——你自己的網址、自己的資料庫、
自己的運算資源，跟原作者完全無關，也不會用到原作者的任何額度。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/CoolAI-Studio/Stock-trading-app)

**只要部署一次。** 同一個服務同時供應 API 和畫面，所以沒有「前端」「後端」兩份要管、
也沒有兩個網址要對起來。

這是**最短的一條路**，不是唯一的一條：Render 和 Neon 都有免費方案而且不用信用卡，
所以按鈕指向它們。要用別家（或自己的機器）完全可以，往下看「不想用這三家？」。

想把畫面另外放在 Vercel／Netlify／Cloudflare 也照樣可以——把 `frontend/` 部署過去，
設 `VITE_API_BASE_URL` 指向你的後端網址就好。**拿掉的是「必須部署兩次」這個要求，
不是「可以分開放」這個選擇。**

**步驟：**

1. **先開資料庫。** 你需要一個 Postgres 連線字串（`postgresql://` 開頭）。免費的例如
   [Neon](https://neon.tech)、[Supabase](https://supabase.com)，註冊後建一個資料庫、把它給你的
   連線字串複製起來。這是唯一沒辦法幫你自動產生的東西，因為它是別人家的服務。

2. **點 Deploy to Render**（後端）。登入／註冊你自己的帳號。它會問你幾個欄位：
   - `DATABASE_URL`：貼上剛剛那串。
   - **其他全部留白就好**，包括加密金鑰和推播金鑰——下一步在網頁上按鈕產生，不用在自己電腦上
     裝任何東西。
   - `PUBLIC_BASE_URL` 也留白：系統會自動用平台給這個服務的網址。

   部署完成後你會拿到一個後端網址。

3. **點 Deploy with Vercel**（前端）。只要填一個變數：
   - `VITE_API_BASE_URL` = 上一步那個後端網址。

   （即時報價用的 WebSocket 網址會自動從這個推導出來，不用另外填。）

4. **打開你的前端網址**。畫面會自動帶你到**設定頁**，而不是給你一個壞掉的登入畫面。
   那一頁會分成兩區：
   - **「現在完全不能用」**：加密金鑰之類的，每一個都有一顆「產生」按鈕，按了複製、
     貼回你的部署平台的環境變數頁。
   - **「不會擋住啟動，但沒填就會有東西不能用」**：主要是 `CORS_ORIGINS`。
     **這一格的值設定頁會直接印出來給你複製**——你正在看的這個網址就是它要的東西。

   每一項都標了步驟編號、不填會怎樣、去哪裡拿。**設定頁會照你實際用的平台講話**：
   它認得出自己跑在 Render、Railway、Fly.io、Heroku、Koyeb 上，就照那一家的選單講；
   認不出來就講通用的說法，不會叫你去一個不存在的頁面。

5. **建立你的第一個帳號**，開始用。完整說明見 [`DEPLOYMENT.md`](./DEPLOYMENT.md)。

### 不想用這三家？

沒有任何一個地方寫死它們。這個 app 需要的是三樣東西，不是三個品牌：

| 要的東西 | 免費的例子 | 換成別的可以嗎 |
| --- | --- | --- |
| 一個能跑 Docker 容器的地方（後端） | Render | 可以。Railway、Fly.io、Koyeb、Heroku、或你自己的機器都行；映像檔就是 `backend/Dockerfile` |
| 一個 Postgres | Neon、Supabase | 可以。任何 Postgres 都行，包含付費方案和自架的 |
| 一個放靜態網站的地方（前端） | Vercel | 可以。Cloudflare Pages、Netlify、任何 CDN，甚至自己的 nginx |

`render.yaml` 是給 Render 那顆按鈕用的設定檔，別家用不到它，也不會因為它而出問題。
換平台唯一要注意的是：這個後端**只能跑一個行程**（`--workers 1`），因為背景盯盤的迴圈
是行程內的單例，開兩份會把每一個訊號通知兩次。

**兩件要先知道的事：**

- **免費方案通常閒置一段時間就會休眠**（Render 是 15 分鐘），休眠時背景監控不會跑，
  也就不會有提醒。DEPLOYMENT.md 第 4 節教你用免費的 UptimeRobot 每 5 分鐘 ping 一次保持喚醒。
  對一個提醒型產品這一步不能省。付費方案通常沒有這個問題。
- **AI 輔助是選填的。** `AI_API_KEY` 和 `AI_MODEL` 留白就是沒有這個功能，其他一切照常；
  金鑰是你自己的，每次發問都算在你自己的帳上。

## 開發

- `backend/`：FastAPI 後端，見 `backend/pytest.ini`、`backend/requirements.txt`
- `frontend/`：Vite + React 前端，見 `frontend/package.json`
