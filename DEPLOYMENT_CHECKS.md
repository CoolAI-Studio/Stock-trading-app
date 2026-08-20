# 部署前後要確認的幾件事

這份清單只放「弄錯了不會有人告訴你」的項目。會噴錯的東西不需要清單。

## 後端（Render）

| 環境變數 | 弄錯的後果 |
| --- | --- |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | 兩者必須是同一對。不是同一對時 Apple 對**每一則**推播回 403 VapidPkHashMismatch，而 app 照常開機、健康檢查照常通過、管道建立起來還回報成功。現在開機時會驗證並拒絕啟動，所以這一項現在是「會噴錯」的了。換金鑰會讓**所有既有訂閱失效**，每台裝置都要重新設定推播。 |
| `VAPID_SUBJECT` | 必須是 `mailto:你的信箱` 或 `https://...`。`render.yaml` 已改成 `sync: false`，要自己填。填成範例位址（`you@example.com` 之類）不會擋開機，但每次啟動會記一條警告 —— 推播服務有權拒絕一個聯絡不到的聯絡方式。 |
| `PUBLIC_BASE_URL` | 這個服務自己的 `https://....onrender.com`。TradingView 設定頁用它顯示 webhook 網址；沒設會顯示 localhost，於是 webhook 永遠不會到，而畫面上沒有任何線索。 |
| `CORS_ORIGINS` | 前端的 Vercel 網址。錯了的話前端每個請求都被瀏覽器擋掉。 |

**出口連線**：後端必須連得到 `*.push.apple.com`（Apple 的推播端點）。Render 目前沒有出口限制，但如果哪天加了，這是第一個會壞掉的東西。

## 前端（Vercel）

**`vercel.json` 的 CSP `connect-src` 必須包含後端的來源。** 目前是
`https://*.onrender.com`，涵蓋現在的後端。

這一條特別容易漏，因為壞掉的方式很安靜：service worker 顯示通知之後會 POST 一個
送達回報回來，那個請求同樣受 CSP 約束。`connect-src` 沒涵蓋後端時，回報會被瀏覽器
**靜默擋下**，「測試」按鈕從此永遠顯示「沒有回報收到」，但通知其實有正常送到。
**換自訂網域時記得一起改。**

## 推播（iPhone）

- iOS 只有**加入主畫面**之後才能收推播（Apple 的規則，從 iOS 16.4 起至今不變）。
  在 Safari 分頁裡連推播 API 都不存在。
- 加入主畫面時不要關掉「開啟為網頁 App」，關掉就只是書籤，一樣收不到，但外觀跟
  成功安裝一模一樣。
- 主畫面只留**一個**圖示。同一個網站裝兩份在 iOS 上是兩個各自獨立的 app（獨立
  儲存、獨立權限、獨立訂閱），會出現「我明明設定過卻收不到」。
- 離線時 APNs 每個 app 只保留**一則**通知，而本專案的 TTL 是一小時 —— 手機關機或
  沒訊號超過一小時，那段期間的提醒不會補送。這是刻意的取捨（一小時前的價格提醒
  沒有意義），但它是一條真實的「提醒沒送到」路徑，所以不要把 Web Push 當成唯一的
  通知管道。

## 每次部署後

```bash
python backend/scripts/watchdog.py https://<你的後端>.onrender.com/healthz
```

回 0 代表資料庫、背景 worker、行情抓取都正常。GitHub Actions 也會每 15 分鐘自動
跑一次（repository variable `HEALTH_URL`），失敗會寄信。
