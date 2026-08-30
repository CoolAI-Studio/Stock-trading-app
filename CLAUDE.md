# 開發規範

## 目前專案

正在把桌面版 tkinter 交易程式改寫成網頁版（FastAPI 後端 + React 前端）。完整架構、資料模型、
分階段建置順序見維護者本機的計畫檔（刻意不放進版控：這個 repo 是公開的，而完整路徑會連帶洩漏本機的使用者名稱）。

## 這是什麼產品

**提醒系統，不是下單系統。** 它盯盤、跑策略、在事情發生時通知使用者。真正下單交給使用者原本在用的
券商 App（用連結帶過去），這個專案不做券商 API 串接。因此：

- **警告不能停擺是最高優先。** 通知沒送到，是這個產品的重大失效；少一個委託類型不是。
- 不要規劃或提案券商 API 整合（元大／新光／Firstrade SDK、委託類型／限價／效期欄位、券商端
  委託回報、刪改單），除非使用者重新開啟這個方向。
- 不用寫 Python 就能設定的簡單價格提醒，是**核心功能**，不是加分項。

## 使用者的策略跑在哪裡（#18）

**跑在子行程裡，不在 app 的行程裡。** 固定三個（`app/services/strategy_pool.py`），
用策略 id 取餘數分配。動到這一塊之前先讀那個檔案的檔頭，底下是不可以退回去的三條：

1. **子行程只拿得到白名單那幾個環境變數**（`strategy_worker._KEEP_FROM_PARENT`）。
   用 `subprocess.Popen(env=...)`，不用 multiprocessing——fork 會繼承整個位址空間，
   spawn 會繼承環境變數，兩個都做不到這件事。`app/enums.py` 是零相依的獨立模組，
   **不要把它搬回 `app/models/`**：那會讓子行程為了一個 enum 把整個 ORM 拖進來，
   啟動從 190 毫秒變成 2.3 秒，而重建期間那個 worker 負責的策略是瞎的。
2. **逾時是殺行程，不是放生執行緒。** 子行程裡直接呼叫 `.instance.on_tick(...)`，
   不走 `strategy_runtime._guarded`——留著內層那層守衛會把真正的期限吃掉，那條
   `while True` 就又活下來了。
3. **子行程壞掉不算策略的錯**（`WorkerUnavailable` → `_record_feed_problem`）。走
   `_record_strategy_error` 的話，一次 spawn 失敗在二十五秒後會把使用者每一支策略
   永久停用，而且沒有東西會把它們打開。

4. **一次性的工作（驗證、回測）不排進常駐池。** 池裡活著的是盯盤策略的狀態，而
   `/validate` 是陌生人按一顆按鈕就打得到的。排在一起的話，任何人送一支跑不完的
   策略，就會連帶清掉同一格上真正在盯盤的那幾支的累積狀態。用 `_scratch`，它反
   過來是故意可拋棄的。
5. **記憶體上限設不起來，一律只能是「沒設成」，不可以是行程起不來。** 而且上限如
   果緊到連沙箱都載不起來，要**自己退讓**（`lift_limits`）——不退讓的話每一支策
   略都會永遠停在「行程暫時不可用」，那是警告全面停擺，比完全沒有上限糟得多。

**編譯就是執行。** `compile_strategy` 會跑類別主體和 `__init__`，所以任何「只是先
編一次看看」的地方都是在跑使用者的程式碼，都要在子行程裡、都要有期限。這一點已經
咬過兩次：`_guarded` 只包 `on_tick`／`on_bar`，不包建構式，所以在搬家之前，一支在
`__init__` 裡 `while True` 的策略可以讓 `/validate` 的請求執行緒永遠回不來——而那
個端點只需要一個登入。

已知代價，刻意接受的：同一格上的策略共用一個行程，一支被殺掉，同格其他策略的累積
狀態也沒了（下次呼叫自動重建，不會瞎掉，但要重新暖身）。換到的是記憶體 = 3 × 20 MB
這個**常數**，跟使用者寫幾支策略無關。

平台差異要照顧：`resource.setrlimit` 只有 POSIX 有，線上是 Linux 而開發機是
Windows。跟上限有關的測試分兩層——「每個平台都要活著」是所有人都跑，「上限真的擋
得住」標 `skipif(win32)`。寫成單一層就會變成本機綠 CI 紅，或反過來。

測試要**隔著管線問**，不要伸手進 `.instance`——那個實例不在這個行程裡。已經因此改
過四條測試，而改完的版本問的是行為（「它真的等了 20 個價才說話」）而不是實作細節
（「那個 dict 裡寫著 20」）。

## 更新不可以停掉已經在跑的那一份（#50）

使用者的副本是從這個 repo 部署的。所以**我們每一次改動，都是別人機器上的一次更
新**——而他不在場、沒有 CI、也不知道我們改了什麼。

兩條已經修好、不可以退回去的：

1. **編譯失敗要先問「它在上一個版本編得過嗎」**（`_record_compile_failure`）。編得
   過就是我們動了什麼，不累積、不停用；沒編過或版本沒變才算他的錯。走錯邊的後果不
   對稱：當成他的錯而其實是我們的，二十五秒後他每一支策略永久停用、沒有東西會打
   開、畫面只寫「停用」。
2. **新設定一律要有預設值。** 能讓開機停住的只有 `_REQUIRED_SECRETS` 那兩個，而它
   們在任何跑得起來的實例上早就設好了。加第三個之前先讀
   `tests/test_an_update_cannot_lock_a_running_deployment.py` 的檔頭——判準是「少了
   它會做出比不服務更糟的事」，不是「這個功能會壞掉」。後者給預設值，讓功能自己說
   它沒設好。

更新怎麼送到他手上（#52）：

- **後端**追 `stable` ＋ autoDeploy。`stable` 只在 `ci.yml` 的 deploy job **最後一
  步**前進，那時候已經確認過測試綠燈、部署送達、線上健康——所以他拿到的每一版，都
  是我們自己的實例已經跑起來而且活著的那一版。不 force：回滾就是把 `stable` 移回
  去，而那必須是人按的。

  **建置設定改了，已經存在的服務不會自己跟上。** 平台上那個服務的 `dockerContext`／
  `dockerfilePath` 是**建立當下抄過去的一份**，不是每次去讀 `render.yaml`。所以動這兩
  格是一種特別的破壞性改動：新部署的人沒事，已經在跑的那些人 build 直接失敗，而症狀
  在我們這邊只看得到「部署沒送達」。#53 就是這樣紅的一次——CI 綠、映像檔建得起來、
  Render 收下請求回 200，然後什麼都沒發生。改這兩格要一併去後台把已經存在的服務改掉。

  更糟的是它會**死結**，而 #53 真的踩進去了。改 `render.yaml` 會觸發 Blueprint 同步，
  而同步是整份套用的——它把 `dockerContext: .` 推上去的同時，也把 `branch: stable` 推
  上去了。於是我們自己的服務從追 `main` 變成追 `stable`，而 `stable` 那一版的
  Dockerfile 寫的是 `COPY requirements.lock .`（只在 context 是 `./backend` 時成立），
  用新的 context 建必然失敗。而 `stable` 只在部署成功之後才前進——三者互相等。

  **所以不要讓部署去挑要建哪一個 commit。** CI 的 deploy hook 現在帶
  `?ref=$GITHUB_SHA`，指名剛剛通過全部測試的那一個，跟後台追哪個分支無關。這一條比
  「記得去後台把分支改回 main」可靠：後者會被下一次 Blueprint 同步再蓋掉一次。
- **前端**是 Vercel 的 `new/clone`，會在他的帳號下複製一份 repo，來源是斷的。
  `.github/workflows/sync-from-upstream.yml` 每天從上游 `stable` 快轉；那個工作流程
  **只快轉、絕不覆蓋**（他改過程式碼是他的權利），而且用 `github.repository` 擋住不
  在上游自己身上跑。
- **同步可能不會發生**（Actions 沒開、有衝突、他改過），所以前端把自己的 commit 印
  在系統狀態頁上跟上游比。**沒更新到這件事要在 app 裡看得見**，不是只在一個他不會
  打開的 Actions 分頁裡。

三個地方都要遵守同一條：**「不知道」不可以顯示成「已經是最新」。** 說成最新會讓他錯
過安全修補，說成有新版會讓他為了一個不存在的更新去重新部署。

## 送到瀏覽器的東西裡不可以有秘密

Vite 會把**每一個 `VITE_` 開頭的環境變數**打進 bundle，而那個 bundle 是公開的。
`VITE_AI_API_KEY` 這種名字一旦出現，金鑰就在每一個訪客的原始碼裡——而且不會有任何東
西變紅。`vite.config.ts` 的 `define` 是同樣公開的第二條管道，而且更隱蔽（連前綴都沒
有）。兩條都由 `frontend/src/test/noSecretsInTheBundle.test.ts` 守著。

後端的 `scripts/audit.py` 看的是 HTTP 回應和資料表，看不到前端的建置產物。

## 使用者是誰（這決定很多事）

**不是工程師。** 是一個想在手機上收到股票提醒的人，他按 README 上的「Deploy to
Render」部署一份自己的副本——自己的網址、自己的資料庫、跟原作者完全無關。

由此推出的兩條規則：

1. **永遠不要叫他去別的地方拿一個值。** app 生得出來的（加密金鑰、推播金鑰）就在
   設定頁給按鈕；真的生不出來的（資料庫在別人家的服務上）就老實說，不要假裝。
   任何「請在你的電腦上跑這支腳本」的指示，對這個使用者等於流程到此結束。
2. **AI 輔助不能是設定流程的必需品。** AI 需要 `AI_API_KEY`，那本身就是一格空白；
   讓設定依賴它就循環了。設定由設定頁解釋，AI 是設定完之後幫忙看問題的。

同一條規則也否決了 Prometheus/Grafana：它會是部署表單上的第八個空格，換來一個
這個使用者不會打開的儀表板。

## 執行紀律（務必遵守）

0. **工程標準（不可退讓的底線）。** 每一項都要**真的在跑**，不是寫在文件上。
   後面標記的是目前狀態，做到了就把標記改掉；沒做到的不要當成已完成。

   | 項目 | 這個專案的具體要求 | 現況 |
   | --- | --- | --- |
   | **IEEE 12207** | 需求→設計→實作→驗證→維運全程有跡可循；決策寫進 commit message 和 `CLAUDE.md`，不留在對話裡 | 進行中 |
   | **TDD** | 先紅後綠。每個功能先寫會失敗的測試，確認它為**正確的原因**失敗，才動實作 | ✅ |
   | **Linter** | 後端**用 CI 的那兩句原句**：`ruff check app tests` ＋ `ruff format --check app tests alembic`——`alembic` **只在第二句裡**，所以用 `ruff format app tests scripts` 這種自己想的路徑，遷移檔永遠測不到，而 autogenerate 出來的遷移檔預設就不是 ruff 的格式（已經因此紅過一次）。前端是 **`npm run build`**（等於 `tsc -b` + vite build）+ `oxlint`。前端不要只跑 `tsc --noEmit`——它不涵蓋測試檔，漏掉的型別錯誤會等到 CI 的 build 步驟才爆。除錯先看 linter，不要用 print 猜。**兩關開真的瀏覽器**：`npm run test:chart`（CI 的 `chart` job）守圖表——替身和 jsdom 都看不到「選項被吃進去了但行為不如預期」，那已經讓一個修好的 bug 帶著全綠的 CI 上線過一次；`npm run test:firstrun`（CI 的 `first-run` job）走一遍全新使用者的路（全空部署 → 設定頁 → 建立第一個帳號 → 引導），它抓到的四件事沒有一件會讓別的 job 變紅。**兩關都不擋 `deploy`**：警告不能停擺優先於畫面 | ✅ |
   | **Git** | 每個邏輯單元一個 commit，訊息寫清楚「為什麼」而不只是「改了什麼」 | ✅ |
   | **CI/CD** | GitHub Actions 每次 push 跑完整套件。**CI 綠燈才算綠燈**，本機跑過不算數。要在推之前先確認，就把 `backend/.env` 和 `trading_app_dev.db` 暫時移開再跑一次——CI 沒有這兩個，而它們已經三次讓本機綠、CI 紅 | ✅ CI ＋ CD（`.github/workflows/ci.yml` 的 `deploy` job：三個 job 全綠才呼叫 Render Deploy Hook。刻意**不用** Render 自己的自動部署——它在 push 時就觸發，會送出測試還沒跑完的 commit）。**部署有沒有真的送達也是自動確認的**：hook 回 201 只代表對方收下請求，所以 deploy job 之後會一直問線上 `/healthz` 的 `version.commit`，等到它變成剛推的那一個才算成功，等不到就紅燈——舊版的後端每一項健康檢查都是綠的，沒有這一步就看不出部署失敗 |
   | **DevSecOps** | 安全左移：相依套件漏洞掃描（Dependabot/`pip-audit`）、密鑰不進版控、CI 內做 SAST、部署前檢查設定 | ✅ Dependabot、CI 內 pip-audit、開機檢查密鑰、SAST（`bandit -r app scripts`，CI 內是硬性關卡；抑制一定要附 `# nosec BXXX` 加理由）、**常態資料外流稽查**（`backend/scripts/audit.py`：CI 每次 push 的硬性關卡 ＋ 每週排程 ＋ 唯讀稽查線上那一份。它不知道這個 app 有什麼——端點從跑起來的 app 讀、資料表從模型登記簿讀——所以新增一個沒有帳號閘門的端點會直接紅燈。做法與三層機制見 `AUDIT.md`） |
   | **需求追蹤** | GitHub Issues 當單一事實來源；每個 commit / PR 連回一個 issue，缺口清單逐項建票。**commit 訊息尾端加 `Refs #N`**（修好的加 `Closes #N`）——這一列跟其他列不一樣：TDD／linter／CI 做不到會有東西變紅，需求追蹤做不到不會有任何東西變紅，它只會讓「為什麼要做這件事」慢慢從版控裡消失。刻意**不**在 CI 加硬性檢查：擋住推送會連帶擋住修通知路徑的 hotfix，而警告不能停擺優先 | ✅ 樣板（`.github/ISSUE_TEMPLATE/` 缺口／故障、`.github/PULL_REQUEST_TEMPLATE.md`）＋ `Refs #N` 慣例。缺口清單已逐項建票 |
   | **Docker / IaC** | 容器已有（`backend/Dockerfile`），相依套件用 `requirements.lock` 鎖版、CI 與映像檔裝同一份；雲端資源（Render / Neon / Vercel）改用 Terraform 宣告，不要靠手點介面 | ⚠️ Docker ✅、鎖版 ✅、Terraform ⚠️（`infra/` 宣告了維護者自己那一份 Render／Neon／Vercel，**但沒有被實際跑過**——寫的時候沒有雲端 token。provider 和資源名稱查過 registry，欄位細節要以 `terraform plan` 為準。**第一次一定是 import 不是 apply**，見 `infra/README.md`。驗得到的只有安全性質：放資料的東西不能被刪、token 不在檔案裡、state 不進版控——`test_the_infra_declaration_cannot_destroy_the_database.py`。**這個目錄不在使用者的路徑上**：README 不提 Terraform，一鍵部署仍然是 render.yaml ＋ 按鈕） |
   | **可觀測性** | 看得到 worker 心跳、行情抓取成敗、通知送達率。**警告不能停擺，就必須看得到它有沒有在跑** | ✅ 外部看門狗（GitHub Actions 每 15 分鐘打 `/healthz`，掛了寄信；repo variable `HEALTH_URL`）＋ app 內建系統狀態頁（`/system`：worker 心跳、行情抓取成敗、逐代號缺價、通知送達分類）。**Prometheus／Grafana 刻意不做**：指標端點要有人抓才算數，而那需要 Grafana Cloud 帳號和部署表單上的第八個空格，換來一個目標使用者不會打開的儀表板。理由（看得到它有沒有在跑）已由前兩者滿足 |

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
     這台機器上可能還有別的專案的 pytest 長得一模一樣，殺錯就毀掉別人正在跑的東西。
   - `Windows fatal exception: stack overflow`、`[vitest-pool] Failed to start threads worker`、
     單一測試 5000ms 逾時——這些都是記憶體不足的症狀，不是程式的 bug。**先重跑再診斷。**
   - 閒置的重量級程式（Docker Desktop 光是開著就吃約 700 MB）用不到就**停用**，不要讓它一直
     佔著、等別的程式要用時才爆掉。**是停用不是移除**——關掉 Docker Desktop 加 `wsl --shutdown`
     只釋放記憶體，安裝檔、映像檔、設定全部原封不動，要用時從開始選單開起來就好。
