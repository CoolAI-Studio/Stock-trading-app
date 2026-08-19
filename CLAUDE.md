# 開發規範

## 目前專案

正在把桌面版 tkinter 交易程式改寫成網頁版（FastAPI 後端 + React 前端）。完整架構、資料模型、
分階段建置順序見計畫檔：`C:\Users\Corey Chan\.claude\plans\memoized-tickling-eich.md`。

## 執行紀律（務必遵守）

1. **TDD 為主。** 每個功能/每個 Phase 都要先有測試，`pytest`（或對應測試指令）結果為**綠燈**才能
   進行下一步。不可以在測試沒過的狀態下堆疊下一層功能。
2. **debug 用 Linter/靜態分析工具**（例如 `ruff`/`pylint`/前端的 `eslint`），不要用亂改、亂試、
   加 print 猜的方式除錯。看 linter 訊息找 root cause 再修。
3. **自主執行，不要一直問。** 除非遇到：
   - 真正的方案選擇（例如要接 A 券商還是 B 券商、要用哪個部署平台）
   - 討論完的規格/計畫本身有問題需要重新確認方向
   否則自己判斷、直接做，不需要每一步都停下來問使用者。
4. **全程使用繁體中文溝通。**
5. **記憶體要省著用，用完立刻釋放。** 這台機器實際可用記憶體常常只剩 6～8 GB，測試套件大到
   最後那幾 GB 就決定跑不跑得完。跑任何測試、開任何背景代理之前，先讀 `memory-discipline`
   skill 並照做。三條底線：
   - 前端一律 `npx vitest run --maxWorkers=1`，前後端測試不同時跑，跑測試時不要同時開背景工作。
   - 中斷或崩潰的執行會留下佔記憶體的孤兒程序，事後一定要清；**但只能清命令列指向本專案的**，
     這台機器上還有別的專案（NextERP）的 pytest 長得一模一樣，殺錯就毀掉別人正在跑的東西。
   - `Windows fatal exception: stack overflow`、`[vitest-pool] Failed to start threads worker`、
     單一測試 5000ms 逾時——這些都是記憶體不足的症狀，不是程式的 bug。**先重跑再診斷。**
   - 閒置的重量級程式（Docker Desktop 光是開著就吃約 700 MB）用不到就**停用**，不要讓它一直
     佔著、等別的程式要用時才爆掉。**是停用不是移除**——關掉 Docker Desktop 加 `wsl --shutdown`
     只釋放記憶體，安裝檔、映像檔、設定全部原封不動，要用時從開始選單開起來就好。
