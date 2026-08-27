# 這裡只放**不是秘密**的東西。token 走環境變數（見 providers.tf）。

variable "render_owner_id" {
  description = "Render 的 owner id（團隊或個人帳號）。不是秘密，但也沒必要寫死。"
  type        = string
}

variable "backend_name" {
  description = "Render 上那個後端服務的名字。要跟現在跑著的那一個一模一樣，否則 import 對不上。"
  type        = string
  default     = "trading-app-backend"
}

variable "repo_url" {
  description = "後端從哪個 repo 部署。"
  type        = string
  default     = "https://github.com/CoolAI-Studio/Stock-trading-app"
}

variable "release_branch" {
  description = <<-EOT
    後端追哪一條分支。

    使用者的副本追 `stable`（見 render.yaml 的註解：那條線只在 CI 全綠、部署送達、
    線上健康之後才前進）。**維護者自己這一份追 `main`**——我們是先當白老鼠的那一個，
    而 stable 是白老鼠活下來之後才前進的線。
  EOT
  type        = string
  default     = "main"
}
