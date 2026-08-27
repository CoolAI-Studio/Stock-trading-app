# 維護者自己那一份基礎設施的宣告。
#
# ＊ 這個目錄**不在使用者的路徑上**。
#
# README 不提 Terraform，也不會提。這個 repo 的讀者是按一顆按鈕部署自己一份的非工程
# 師，而 `terraform apply` 是終端機指令——CLAUDE.md 說得很明白，「請在你的電腦上跑這
# 支腳本」對那個人等於流程到此結束。
#
# 一鍵部署仍然是 render.yaml ＋ Vercel 按鈕，而且會一直是。
#
# ＊ 版本釘死。
#
# provider 是別人寫的程式碼，而它握有刪掉這幾個資源的權限。一個沒有釘住的版本會在
# 某次 init 之後變成另一份程式碼，而那次 init 通常發生在「我只是想看看 plan」的時候。

terraform {
  required_version = ">= 1.6"

  required_providers {
    render = {
      source  = "render-oss/render"
      version = "~> 1.9"
    }
    # 社群的，不是 Neon 官方的——registry 上沒有 neondatabase/neon。
    # 這件事要寫出來：把社群 provider 當成官方的，會讓人以為它的相容性保證比實際多。
    neon = {
      source  = "kislerdm/neon"
      version = "~> 0.15"
    }
    vercel = {
      source  = "vercel/vercel"
      version = "~> 5.14"
    }
  }
}

# 三家的 token 都走環境變數，一律不進檔案：
#
#   RENDER_API_KEY
#   NEON_API_KEY
#   VERCEL_API_TOKEN
#
# 這個 repo 是公開的，而一個寫進 .tf 或 .tfvars 的 token 就是一個公開的 token。
# tests/test_the_infra_declaration_cannot_destroy_the_database.py 守著這一條。
provider "render" {
  # owner_id 不是秘密（它是團隊的識別碼），但也沒有理由寫死在這裡。
  owner_id = var.render_owner_id
}

provider "neon" {}

provider "vercel" {}
