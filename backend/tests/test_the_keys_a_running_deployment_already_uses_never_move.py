"""這兩把金鑰換掉，等於把已經在跑的那一份**清空**，而且不會有任何錯誤訊息。

CLAUDE.md：「我們每一次改動，都是別人機器上的一次更新——而他不在場、沒有 CI、也不
知道我們改了什麼。」這個檔案守的是那條規則裡代價最大的一種。

＊ 兩把金鑰都是從同一個環境變數推導出來的。

`SECRET_ENCRYPTION_KEY` 是部署平台自動產生的一串隨機字元。它形狀不合 Fernet，所以
`config.fernet_key` 用 scrypt 把它推成一把；`config._derive_vapid` 再用**不同的鹽**
從同一顆種子推出一對 P-256 推播金鑰。使用者一格都不用填，這是刻意的設計。

代價是：**推導方式本身變成了資料格式的一部分。**

＊ 改掉的話會發生什麼事。

改 `_KDF_SALT`、改 scrypt 的 n/r/p、改 length、換一個 KDF、甚至只是把
`value.encode("utf-8")` 改成別的編碼——任何一個都會讓同一顆種子推出不同的金鑰，而
在他那一份已經跑了三個月的部署上：

    通知管道的 bot token、chat id、SMTP 密碼、AI 的 API 金鑰
        → 全部是用舊金鑰加密存著的
        → 新金鑰解不開
        → 每一個管道都送不出去

    手機上的推播訂閱
        → 是用舊的 VAPID 公鑰在瀏覽器裡建立的
        → 新私鑰簽的推播，Apple／Google 回 403 VapidPkHashMismatch
        → 推播照送、對方拒收、**沒有人看得到**

兩件事一起發生的意思就是：他的提醒全面停擺，而畫面上什麼都沒變紅。這是這個 repo
定義過的「重大失效」的最完整形態。

＊ 為什麼原本沒有東西擋得住。

已經有的那條測試是

    assert fernet_key(PLATFORM_GENERATED) == fernet_key(PLATFORM_GENERATED)

它問的是「同一個行程裡算兩次會不會一樣」。改鹽、改參數、換 KDF——每一種都照樣讓那
一條是綠的，因為兩邊會一起改。**沒有任何一條測試記得上一版算出來的是什麼。**

所以這裡把答案寫死。這不是在測 scrypt 對不對，是在測**它有沒有動過**。

＊ 這一條紅了要怎麼辦。

先假設不是這個檔案錯了。真的必須換推導方式的話，那是一次資料遷移，不是一次改參
數：舊金鑰要留著、把資料庫裡每一筆重新加密、推播訂閱要作廢並請使用者重新訂閱。在
那一整套做出來之前，這裡的期望值不可以動。
"""

import base64

import pytest

from app.config import _derive_vapid, fernet_key

# 隨便一顆種子，形狀跟部署平台 generateValue 給的一樣（夠長、純隨機字元）。
# 值本身不重要，重要的是它推出來的東西**永遠不變**。
SEED = "a-platform-generated-value-32chars"

# 下面三個值是 2026-09-04 當下的推導結果。它們不是「應該是多少」算出來的，是
# 「現在就是多少」量出來的——因為要守的性質正是「跟上一版一樣」。
FERNET = b"SwdFboZB-SL2zp7wpEZtK5mJAhyCctrF5WkfxgGcqwY="
VAPID_PUBLIC = (
    "BOQleTQ95XXfsoKGUtmCHhsQUy9wGrAPjZoM4TypLehd-XS8yJyV4YHzikRmJa2CuP8KChpTnh3JHXp6sDlvwdc"
)
VAPID_PRIVATE = "B3PzOTPRKTigcMgyNm391kT43qrnqCNizXV8cDeX4Cg"


def test_the_encryption_key_derived_from_a_seed_never_changes():
    """他資料庫裡每一個秘密都是用這把金鑰加密的。

    換一把＝那些欄位全部解不開＝每一個通知管道送不出去，而且沒有錯誤訊息。
    """
    assert fernet_key(SEED) == FERNET


def test_the_push_keys_derived_from_a_seed_never_change():
    """他手機上的訂閱是用舊公鑰建立的。

    換一對＝Apple／Google 回 403 VapidPkHashMismatch＝推播照送、對方拒收、
    沒有人看得到。
    """
    assert _derive_vapid(SEED) == (VAPID_PUBLIC, VAPID_PRIVATE)


def test_the_two_derivations_do_not_collide():
    """同一顆種子推出兩把不同用途的金鑰，靠的是不同的鹽。

    鹽被抄成一樣的話，加密金鑰就等於推播私鑰——而推播公鑰是**公開的**，任何一個
    訪客都拿得到。少了這一條，那次抄錯不會有任何東西變紅。
    """
    encryption = base64.urlsafe_b64decode(fernet_key(SEED))
    push_private = base64.urlsafe_b64decode(VAPID_PRIVATE + "=")

    assert encryption != push_private


@pytest.mark.parametrize(
    "already_a_fernet_key",
    [
        # 早期的部署是使用者自己在設定頁按「產生」、貼進平台的，所以環境變數裡
        # 本來就是一把合法的 Fernet 金鑰。
        base64.urlsafe_b64encode(b"\x00" * 32).decode(),
        base64.urlsafe_b64encode(bytes(range(32))).decode(),
    ],
)
def test_a_key_that_is_already_a_fernet_key_is_never_re_derived(already_a_fernet_key):
    """「順手也推導一次」會讓最早那一批使用者的資料全部解不開。

    他們的環境變數裡就是這種值，而資料庫裡有用它加密過的東西。
    """
    assert fernet_key(already_a_fernet_key) == already_a_fernet_key.encode()
