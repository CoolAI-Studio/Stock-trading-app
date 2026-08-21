# Stock Trading App

個人股票交易儀表板：網頁化的策略監控、手動確認下單、TradingView Webhook 訊號、即時報價推播、
LINE/Telegram/Email 通知。後端 FastAPI + SQLAlchemy，前端 Vite + React + TypeScript。

## 想要自己的一份嗎？一鍵部署

點下面兩個按鈕，會分別帶你去 **Render** 跟 **Vercel** 官方網站，用你自己的帳號部署一份完全獨立
的系統——你自己的網址、自己的資料庫、自己的運算資源，跟原作者完全無關，也不會用到原作者的任何
額度。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/CoolAI-Studio/Stock-trading-app)
[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/CoolAI-Studio/Stock-trading-app&root-directory=frontend&env=VITE_API_BASE_URL,VITE_WS_URL&envDescription=VITE_API_BASE_URL%20%2F%20VITE_WS_URL%20%E8%AB%8B%E5%A1%AB%E4%BD%A0%E9%83%A8%E7%BD%B2%E5%A5%BD%E7%9A%84%20Render%20%E5%BE%8C%E7%AB%AF%E7%B6%B2%E5%9D%80)

**你需要三個免費帳號**：Render（後端）、Vercel（前端）、Neon（資料庫）。三個都不用信用卡。

**步驟：**

1. **先開資料庫。** 去 [neon.tech](https://neon.tech) 註冊、建一個資料庫，把它給你的連線字串
   （`postgresql://` 開頭）複製起來。這是唯一沒辦法幫你自動產生的東西，因為它是別人家的服務。

2. **點 Deploy to Render**（後端）。登入／註冊你自己的 Render 帳號。它會問你幾個欄位：
   - `DATABASE_URL`：貼上剛剛那串。
   - **其他全部留白就好**，包括加密金鑰和推播金鑰——下一步在網頁上按鈕產生，不用在自己電腦上
     裝任何東西。
   - `PUBLIC_BASE_URL` 也留白：系統會自動用 Render 給這個服務的網址。

   部署完成後你會拿到一個網址，例如 `https://your-app.onrender.com`。

3. **點 Deploy with Vercel**（前端）。畫面會要你填兩個變數，兩個都填**上一步那個 Render 網址**：
   - `VITE_API_BASE_URL` = `https://your-app.onrender.com`
   - `VITE_WS_URL` = 同一個網址，但開頭改成 `wss://`

4. **打開你的前端網址**（例如 `https://your-app.vercel.app`）。畫面會自動帶你到**設定頁**，
   而不是給你一個壞掉的登入畫面。那一頁會分成兩區：
   - **「現在完全不能用」**：加密金鑰之類的，每一個都有一顆「產生」按鈕，按了複製、貼回
     Render 的 Environment 頁面。
   - **「不會擋住啟動，但沒填就會有東西不能用」**：主要是 `CORS_ORIGINS`——把你的 Vercel
     網址貼進去。這一格一定是最後填的，因為在前端存在之前沒有人知道那個網址。

   每一項都標了步驟編號、不填會怎樣、去哪裡拿。存回 Render 之後它會自動重新部署，
   一兩分鐘後重新整理就好。

5. **建立你的第一個帳號**，開始用。完整說明見 [`DEPLOYMENT.md`](./DEPLOYMENT.md)。

**兩件要先知道的事：**

- **Render 免費方案閒置 15 分鐘會休眠**，休眠時背景監控不會跑，也就不會有提醒。DEPLOYMENT.md
  第 4 節教你用免費的 UptimeRobot 每 5 分鐘 ping 一次保持喚醒。對一個提醒型產品這一步不能省。
- **AI 輔助是選填的。** `AI_API_KEY` 和 `AI_MODEL` 留白就是沒有這個功能，其他一切照常；
  金鑰是你自己的，每次發問都算在你自己的帳上。

## 開發

- `backend/`：FastAPI 後端，見 `backend/pytest.ini`、`backend/requirements.txt`
- `frontend/`：Vite + React 前端，見 `frontend/package.json`
