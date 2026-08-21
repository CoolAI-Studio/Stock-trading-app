# Stock Trading App

個人股票交易儀表板：網頁化的策略監控、手動確認下單、TradingView Webhook 訊號、即時報價推播、
LINE/Telegram/Email 通知。後端 FastAPI + SQLAlchemy，前端 Vite + React + TypeScript。

## 想要自己的一份嗎？一鍵部署

點下面兩個按鈕，會分別帶你去 **Render** 跟 **Vercel** 官方網站，用你自己的帳號部署一份完全獨立
的系統——你自己的網址、自己的資料庫、自己的運算資源，跟原作者完全無關，也不會用到原作者的任何
額度。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/CoolAI-Studio/Stock-trading-app)
[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/CoolAI-Studio/Stock-trading-app&root-directory=frontend&env=VITE_API_BASE_URL,VITE_WS_URL&envDescription=VITE_API_BASE_URL%20%2F%20VITE_WS_URL%20%E8%AB%8B%E5%A1%AB%E4%BD%A0%E9%83%A8%E7%BD%B2%E5%A5%BD%E7%9A%84%20Render%20%E5%BE%8C%E7%AB%AF%E7%B6%B2%E5%9D%80)

**步驟：**

1. 先點 **Deploy to Render**（後端），登入/註冊你自己的 Render 帳號，確認建立服務。部署完成後
   會拿到一個網址，例如 `https://your-app.onrender.com`。
2. 再點 **Deploy with Vercel**（前端），登入/註冊你自己的 Vercel 帳號。畫面會要求填入兩個環境
   變數：
   - `VITE_API_BASE_URL` = 剛剛 Render 給你的網址
   - `VITE_WS_URL` = 同一個網址，但開頭改成 `wss://`
3. 兩邊都部署完成後，打開你自己的前端網址，例如 `https://your-app.vercel.app`。

**如果還有欄位沒填，畫面會自動帶你到設定頁**，而不是給你一個壞掉的登入畫面。那一頁會列出還缺
哪些設定、不填會怎樣，而且**能由系統自己產生的值都有一顆按鈕**——加密金鑰、推播金鑰都按一下就有，
不需要在自己電腦上裝 Python 或跑任何指令。產生出來的值複製、貼回 Render 的 Environment 頁面存檔，
Render 會自動重新部署，一兩分鐘後重新整理就好了。

唯一沒辦法幫你產生的是 `DATABASE_URL`——資料庫要去 [neon.tech](https://neon.tech) 開一個免費的，
把它給你的連線字串貼過來。設定頁上也會這樣告訴你。

全部填完之後，那個網址就是你自己獨立系統的登入畫面。

完整的環境變數說明、Neon 資料庫設定、建立第一個帳號、保活設定等細節，見
[`DEPLOYMENT.md`](./DEPLOYMENT.md)（原作者自己部署時走的完整手動步驟，一鍵部署按鈕背後也是同一套
設定，只是自動化了大部分手動操作）。

## 開發

- `backend/`：FastAPI 後端，見 `backend/pytest.ini`、`backend/requirements.txt`
- `frontend/`：Vite + React 前端，見 `frontend/package.json`
