# 維護者自己那一份基礎設施

**這個目錄不在使用者的路徑上。** README 不提 Terraform，也不會提——這個 repo 的讀者
是按一顆按鈕部署自己一份的非工程師，而 Terraform 的每一步都要在終端機裡打指令。一鍵部署仍然
是 `render.yaml` ＋ Vercel 按鈕。

## 第一次：**import，不是 apply**

這幾個資源**已經存在**（它們現在就在跑）。對著一份空的 state 直接 `apply`，Terraform
會認為什麼都還沒有，然後去建立**第二份**——或者在名字衝突時失敗，而失敗是比較好的那
個結果。

```bash
export RENDER_API_KEY=...        # Render → Account Settings → API Keys
export NEON_API_KEY=...          # Neon → Account settings → API keys
export VERCEL_API_TOKEN=...      # Vercel → Settings → Tokens
export TF_VAR_render_owner_id=...

terraform init

# 這兩步**不需要任何 token**，所以它們先跑：
#   init     去 registry 抓 provider，並寫出 .terraform.lock.hcl（版本＋校驗碼）
#   validate 對著真正的 provider schema 檢查每一個欄位名稱
terraform validate

# 把現有的東西接進來。id 從各家後台或 API 拿。
terraform import neon_project.db          <neon-project-id>
terraform import render_web_service.backend <srv-...>
terraform import vercel_project.frontend  <prj_...>

# 這一步才是重點：確認它「什麼都不用做」。
terraform plan
```

`plan` 如果說要建立、刪除或取代任何東西，**不要 apply**。那代表這份宣告跟現況對不
上，而對不上的那一邊是這份宣告。

## 之後

```bash
terraform plan    # 永遠先看
terraform apply
```

## 這份宣告裡刻意沒有管的東西

- **環境變數**（`JWT_SECRET`、`SECRET_ENCRYPTION_KEY`、`DATABASE_URL`…）。它們是秘
  密，而 Terraform 管了它們就等於把它們寫進 state——**state 是明文的**。它們由設定頁
  產生、由 Render 後台保存，那是對的地方。
- **Neon 的 region 和 Postgres 版本**。已經固定下來了，而 provider 的預設值跟現況不
  同就會被規劃成一次變更。

## state 檔

`terraform.tfstate` **不進版控**，`.gitignore` 擋著，而且有一條測試守那一行。

它是明文的，而且會忠實地記下 Neon 給的那一串 `postgresql://使用者:密碼@…`。這個
repo 是公開的。

## 這份宣告沒有被實際跑過

寫它的時候沒有任何雲端 token，所以**它的語法和欄位名稱沒有經過 `terraform init` 驗
證**。provider 和資源型別的名字是從各自的 registry / docs 查到的（`render-oss/render`
1.9.x、`kislerdm/neon` 0.15.x——注意那是社群的，registry 上沒有 Neon 官方的、
`vercel/vercel` 5.14.x），但欄位的細節要以 `terraform plan` 的結果為準。

`backend/tests/test_the_infra_declaration_cannot_destroy_the_database.py` 守得住的只
有安全性質：**放資料的東西不能被刪掉、token 不在檔案裡、state 不進版控**。那幾條是
在沒有 token 的情況下唯一驗得到、也是最值得驗的東西。

## 線上那一份現在真正的設定（2026-08-30 讀出來的）

底下是用 Render API 直接讀到的值，不是推測。**第一次 `terraform plan` 的時候拿這張表
去核**：任何一格 plan 想要改掉，都要先問清楚為什麼，而不是按下去。

| 欄位 | 現在的值 | 錯了會怎樣 |
| --- | --- | --- |
| `dockerContext` | `.` | 這一格剛剛咬過一次。設成 `./backend` 的話 build 直接失敗（`"/backend/requirements.lock": not found`），而在 CI 那邊只看得到「部署沒送達」 |
| `dockerfilePath` | `./backend/Dockerfile` | — |
| `branch` | `main` | 追 `stable` 的話會部署上一版；而 `stable` 只在部署成功之後才前進，所以會**死結** |
| `autoDeploy` | **`no`** | 開著的話 Render 會在 push 當下就部署，送出測試還沒跑完的 commit。CI 的 deploy job 是唯一該部署的路（見 `ci.yml` 的註解） |
| `numInstances` | **`1`** | 大於 1 的話盯盤迴圈會有兩份，**每一個提醒都會送兩次**。這個後端只能跑一個行程 |
| `healthCheckPath` | `/healthz` | 沒設的話 Render 不會知道容器死了 |
| `plan` / `region` | `free` / `oregon` | 換 region 會換網址，而那個網址在使用者手機上、也在 GitHub 的 `HEALTH_URL` 裡 |

`main.tf` 目前只宣告了前三格加 `plan`／`region`。後三格（`autoDeploy`、`numInstances`、
`healthCheckPath`）**刻意沒寫進去**：欄位名稱沒辦法在沒有 token 的情況下驗證，而猜錯一
個名字比留白更糟——留白的話 `terraform plan` 會把差異印出來給人看，猜錯則會安靜地套用
一個不存在的設定。第一次 plan 之後，照 plan 的實際輸出把它們補上。

**怎麼讀這些值**（不需要進後台，也就不會像這次一樣「對到一格就以為兩格都對」）：

```
curl -H "Authorization: Bearer $RENDER_API_KEY" \
  https://api.render.com/v1/services/<srv-id> | jq '.serviceDetails.envSpecificDetails'
```
