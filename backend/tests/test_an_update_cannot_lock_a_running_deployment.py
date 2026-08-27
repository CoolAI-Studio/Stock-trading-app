"""我們加一格新設定，不可以讓一個本來跑得好好的實例停下來。

＊ 這是一條絆線，不是一個功能。

`enforce_required_secrets` 失敗 → app 進入設定模式 → API 上鎖、worker 不跑。對一個
**第一次**部署的人，那是對的：用一把可偽造的 JWT_SECRET 提供服務，比不提供服務糟
得多。

但同一條路對一個**已經跑了三個月**的實例是災難：他更新了一次，而我們在這一版加了
一格新的必填設定——於是他的提醒全部停掉，畫面上是一個設定頁，而他昨天什麼都沒動。

＊ 為什麼是絆線而不是自動判斷。

「這個新設定少了會不會出事」沒有辦法用程式判斷。JWT_SECRET 少了是可偽造的登入權
杖，那必須鎖；一個新功能的 API 網址少了，只是那個功能不能用。兩者長得一模一樣，差
別只在人的判斷裡。

所以這裡把「能讓開機停住的東西」釘住。要加一個，就得**改這份清單**，而改的時候會
讀到這段話。這是這個 repo 對付「安靜地變糟」一貫的做法：讓決定發生在看得見的地方。

＊ 現在能鎖住開機的，全部在這裡。

而它們有一個共通點：**在任何跑得起來的實例上，它們早就已經設好了。** 所以現有的這
幾個不會因為更新而突然缺席——會的是新加的那個。
"""

import pytest

from app.config import Settings, verify_required_secrets

# 少了就必須拒絕開機的。**加東西進來之前先讀這個檔案的檔頭。**
#
# 判準：少了它，這個 app 會做出一件**比不服務更糟**的事。
#
#   JWT_SECRET        任何人都能簽出你帳號的登入權杖
#   TV_WEBHOOK_SECRET 任何人都能貼進假的 TradingView 訊號
#
# 「這個功能會壞掉」不是判準。那種東西給它一個預設值，讓功能自己說它沒設好——
# 而不是讓整個 app 停下來替它說。
LOCKS_THE_BOOT = {"JWT_SECRET", "TV_WEBHOOK_SECRET"}


def _configured(**overrides) -> Settings:
    """一個**已經設定好、正在跑**的部署會有的設定。"""
    values = {
        "JWT_SECRET": "a-real-and-long-enough-secret-value-for-tests",
        "TV_WEBHOOK_SECRET": "another-real-and-long-enough-secret-value",
        "SECRET_ENCRYPTION_KEY": "TZ3EWSJHZfhZ1lxdCkNrjEkqSTAdyBGT4tYSYZfxYic=",
        "VAPID_PUBLIC_KEY": "",
        "VAPID_PRIVATE_KEY": "",
    }
    values.update(overrides)
    return Settings(**values)


def test_the_list_of_things_that_can_lock_the_boot_has_not_grown():
    """加一個進去，這條就紅。

    紅的時候要問的不是「怎麼讓測試變綠」，是「少了它真的比不服務更糟嗎」。如果不
    是，給它一個預設值——**每一個已經在跑的實例都會因為它而停下來，而他們什麼都沒
    做錯**。
    """
    from app.config import _REQUIRED_SECRETS

    assert set(_REQUIRED_SECRETS) == LOCKS_THE_BOOT, (
        "能讓開機停住的東西變了。這會讓每一個已經在跑的部署在下一次更新時停下來——"
        "先讀 tests/test_an_update_cannot_lock_a_running_deployment.py 的檔頭。"
    )


def test_a_configured_deployment_starts():
    """基準線：一個設定好的實例本來就該起得來。

    沒有這一條，上面那條可能因為別的原因而假綠。
    """
    verify_required_secrets(_configured())


def test_push_keys_left_empty_do_not_lock_a_deployment():
    """只用 Telegram 或 email 的人，不必為了開機去申請推播金鑰。

    這是「新設定該長什麼樣」的範本：兩邊都空是一個**有效的設定**，而不是一個錯誤。
    render.yaml 也是這樣寫的。少了它只是手機推播不能用，其他一切照常——那就不該
    鎖住開機。
    """
    verify_required_secrets(_configured(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY=""))


def test_a_half_configured_push_pair_still_refuses():
    """但**半套**的推播設定要擋。

    兩把不成對的金鑰會讓每一次推播被 Apple 回 403，而 app 開機是綠的、健康檢查是綠
    的、頻道建得起來也回報成功——然後一則提醒都沒有送到。那個沉默正是這個產品不能
    有的失效，所以它跟「沒設」是兩件事。
    """
    with pytest.raises(RuntimeError):
        verify_required_secrets(_configured(VAPID_PUBLIC_KEY="only-half-of-a-pair"))


def test_every_new_setting_has_a_default():
    """新設定要有預設值，不然它等於一格必填。

    一個沒有預設值的欄位，在使用者的部署表單上就是一格空白；而他不會知道那格要填
    什麼，因為那是我們上禮拜才加的。

    這一條刻意不列白名單：**每一個**設定都要能在什麼都不給的情況下建出來。真的需要
    使用者提供的東西，走 LOCKS_THE_BOOT 那條路並且在設定頁解釋。
    """
    missing = [
        name
        for name, field in Settings.model_fields.items()
        if field.is_required() and name not in LOCKS_THE_BOOT
    ]

    assert not missing, f"這幾個設定沒有預設值，會變成使用者部署表單上的必填格：{missing}"
