"""基礎設施的宣告本身要擋得住一次手滑。

＊ 為什麼這一組測試存在。

`infra/` 底下那幾個 `.tf` 描述的是**正在跑的那一份**：Render 服務、Neon 資料庫、
Vercel 專案。而寫這些檔案的人（我）沒有任何雲端 token，所以**沒有辦法實際驗證它們
跑起來是什麼樣子**。

一個看起來很像樣但其實寫錯的 Terraform 設定，被 `apply` 下去的後果不是「不會動」，
是**重建 Neon 專案而毀掉資料庫**——而那一份資料庫裡有他所有的策略、部位和通知紀錄。

所以能驗的就要驗，而能驗的正是**安全性質**：

  一、放資料的東西不可以被 Terraform 刪掉或取代（`prevent_destroy`）
  二、token 不可以出現在檔案裡
  三、state 檔不可以進版控——它裡面有資料庫連線字串

第三條特別容易被忽略：`terraform.tfstate` 是明文的，而它會忠實地記下 Neon 給的那一
串 `postgresql://使用者:密碼@...`。

＊ 這一組**不驗語法也不驗它跑不跑得起來**。

那需要 `terraform init`（要下載 provider）和真的 token。CI 上沒有，而在這個專案裡
「本機跑過不算數」。`infra/README.md` 因此把「第一次要 import 而不是 apply」寫成明
確的步驟，而不是一句提醒。
"""

from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parent.parent.parent / "infra"

# 這幾個資源一旦被取代，裡面的東西就沒了。
HOLDS_DATA = ("neon_project", "render_web_service")


def _tf_files() -> list[Path]:
    return sorted(INFRA.glob("*.tf"))


def test_there_is_something_to_check():
    """基準線。infra/ 空了的話，底下每一條都會空轉而通過。"""
    assert _tf_files(), "infra/ 底下沒有任何 .tf，這一組測試等於沒在驗東西"


@pytest.mark.parametrize("kind", HOLDS_DATA)
def test_the_things_that_hold_data_cannot_be_destroyed(kind: str):
    """**這一條是這一組的全部意義。**

    `prevent_destroy` 讓 Terraform 在計畫階段就拒絕任何會刪掉或取代這個資源的操
    作。沒有它，一個打錯的參數（改了一個 Terraform 認為不可變更的欄位）會變成
    「destroy and recreate」——而那對 Neon 專案的意思是資料庫沒了。
    """
    text = "\n".join(path.read_text(encoding="utf-8") for path in _tf_files())
    assert f'resource "{kind}"' in text, f"找不到 {kind} 的宣告"

    block = text.split(f'resource "{kind}"', 1)[1]
    # 只看到下一個 resource 為止。
    block = block.split('\nresource "', 1)[0]
    assert "prevent_destroy = true" in block, (
        f"{kind} 沒有 prevent_destroy。一次 apply 就可能把它刪掉重建——"
        "而那對資料庫的意思是資料沒了。"
    )


def test_no_token_is_written_into_the_files():
    """token 走環境變數，不進檔案。

    三家的 provider 都讀得到 `*_API_KEY` 這類環境變數。把 token 寫進 .tf（或
    `.tfvars`）就是把它交給版控，而這個 repo 是公開的。
    """
    suspicious = []
    for path in [*_tf_files(), *INFRA.glob("*.tfvars")]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 長得像被指派了一個字面值的密鑰。
            if any(word in stripped.lower() for word in ("api_key", "token", "password")):
                if "=" in stripped and '"' in stripped.split("=", 1)[1]:
                    value = stripped.split("=", 1)[1].strip().strip('"')
                    # 指到變數或空字串沒問題，寫死一串才是問題。
                    if len(value) > 8 and not value.startswith(("var.", "${", "local.")):
                        suspicious.append(f"{path.name}:{number}: {stripped}")

    assert not suspicious, "這幾行看起來把密鑰寫進檔案了：\n" + "\n".join(suspicious)


def test_state_is_kept_out_of_version_control():
    """`terraform.tfstate` 是明文，而且裡面有資料庫連線字串。

    它進了版控就等於把 Neon 給的那一串 `postgresql://使用者:密碼@...` 公開——而這
    個 repo 是公開的。
    """
    ignored = (INFRA.parent / ".gitignore").read_text(encoding="utf-8")

    for pattern in ("*.tfstate", ".terraform/"):
        assert pattern in ignored, f".gitignore 少了 {pattern}"


def test_the_runbook_says_import_before_apply():
    """第一次一定要 import，不是 apply。

    這幾個資源**已經存在**（它們現在就在跑）。對著一份空的 state 直接 apply，
    Terraform 會認為什麼都還沒有，然後去**建立第二份**——或者在名字衝突時失敗，
    而失敗是比較好的那個結果。

    這件事寫成 README 的第一段，不是一句附註。
    """
    readme = (INFRA / "README.md").read_text(encoding="utf-8")

    # 比對的是**指令**，不是「import」和「apply」這兩個字。
    #
    # 第一版我比字，而第一段那句「`terraform apply` 是終端機指令」剛好先出現——測試
    # 紅了，但紅的原因不是我要測的那個。讀的人會照著跑的是指令，不是散文。
    assert "terraform import" in readme, "runbook 沒有叫人先 import"
    assert readme.index("terraform import") < readme.index("terraform apply"), (
        "apply 出現在 import 之前——讀的人會先照著 apply 做，而那會去建立第二份"
    )
