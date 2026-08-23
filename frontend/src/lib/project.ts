/**
 * 這個專案自己的來歷：一個陌生人想要自己一份的時候，該去哪裡。
 *
 * **只有一份骨架，散出去的是部署不是拷貝。** 每一份部署的註冊入口都指回同一個
 * repo，理由有三個，第三個是關鍵：
 *
 *   一、安全修補傳得下去。各自拷貝之後，每一份都是拷貝那一刻的快照。這個 app
 *       修過策略沙箱逃逸和跨帳號隔離——層層轉發之後，最末端的人拿到的是少了那些
 *       修補的版本，而他不會知道。
 *
 *   二、拷貝模式要 app 知道「我的原始碼在哪」，那會是部署表單上的第八個空格。
 *       這個專案已經為了同一條理由否決過 Prometheus。
 *
 *   三、「拷貝到自己的空間」在 GitHub 上就是 fork，而 fork 要有 GitHub 帳號。
 *       目標使用者是想在手機上收股票提醒的人，不是工程師。他做得到的是按一顆
 *       Deploy 按鈕，而那顆按鈕指向哪一個 repo 對他沒有差別。
 *
 * 改過這份程式碼的人要指向自己的 repo，就設 `VITE_DEPLOY_URL`（前端建置時的環境
 * 變數，Vercel 那類平台上填一格就好），或直接改下面這個常數——他既然已經在改程
 * 式碼，改一行不是負擔；而沒改的人自動跟著上游更新。
 */
export const DEPLOY_YOUR_OWN_URL =
  import.meta.env.VITE_DEPLOY_URL ??
  'https://render.com/deploy?repo=https://github.com/CoolAI-Studio/Stock-trading-app'
