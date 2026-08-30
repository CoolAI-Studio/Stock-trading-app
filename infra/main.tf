# 正在跑的那三個東西。
#
# **這幾個資源已經存在。** 第一次使用一定是 `terraform import`，不是 `apply`——
# 對著一份空的 state 直接 apply，Terraform 會認為什麼都還沒有，然後去建立第二份。
# 見 README.md。

# --- 資料庫 -----------------------------------------------------------------
#
# 這是整份基礎設施裡唯一一個**弄丟就真的沒了**的東西：使用者的策略、部位、通知紀
# 錄、加密過的券商憑證，全部在這裡面。
resource "neon_project" "db" {
  name = "trading-app"

  lifecycle {
    # **不可以被刪掉或取代。**
    #
    # Terraform 對某些欄位的變更會規劃成「destroy and recreate」，而那對資料庫的
    # 意思是資料沒了。這一行讓那種計畫在執行之前就失敗。
    #
    # 要真的搬遷的話，先手動備份、再暫時拿掉這一行——那個摩擦是刻意的。
    prevent_destroy = true

    # 連線字串、region、pg 版本這些由 Neon 那邊決定或已經固定下來的東西，不要因為
    # provider 的預設值跟現況不同就被規劃成變更。
    ignore_changes = [region_id, pg_version]
  }
}

# --- 後端 -------------------------------------------------------------------
resource "render_web_service" "backend" {
  name   = var.backend_name
  plan   = "free"
  region = "oregon"

  runtime_source = {
    docker = {
      repo_url    = var.repo_url
      branch      = var.release_branch
      dockerfile_path = "./backend/Dockerfile"
      # **根目錄，不是 ./backend。** 這個映像檔同時建前端和後端（#53），所以它要看得
      # 到 frontend/ 和 backend/ 兩邊。
      #
      # 這一格的實測代價：Render 上那個已經存在的服務沒有跟著 render.yaml 更新（建置
      # 設定是建立當下抄過去的一份），build 失敗成
      # `"/backend/requirements.lock": not found`，而在 CI 那邊只看得到「部署沒送
      # 達」。花了六輪才找到，因為 dockerfile_path 是對的，只有 context 不是。
      context = "."
    }
  }

  lifecycle {
    # 後端本身沒有存資料（資料在 Neon），但取代它會換掉網址，而那個網址寫在使用者
    # 手機上的前端設定裡、也寫在 GitHub 的 HEALTH_URL 變數裡。
    prevent_destroy = true

    # 環境變數**不由 Terraform 管**。
    #
    # 它們是秘密（JWT_SECRET、SECRET_ENCRYPTION_KEY、DATABASE_URL），而 Terraform
    # 管了它們就等於把它們寫進 state——而 state 是明文的。它們由設定頁產生、由
    # Render 後台保存，那是對的地方。
    ignore_changes = [env_vars]
  }
}

# --- 前端 -------------------------------------------------------------------
#
# 沒有 prevent_destroy：它是無狀態的，重建只是重新 build 一次。真正不能弄丟的是
# 上面那兩個。
resource "vercel_project" "frontend" {
  name      = "stock-trading-app"
  framework = "vite"

  root_directory = "frontend"

  git_repository = {
    type = "github"
    repo = "CoolAI-Studio/Stock-trading-app"
  }
}
