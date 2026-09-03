"""換掉雜湊演算法，等於把已經在跑的那一份**鎖在門外**——而且他沒有辦法自己開。

CLAUDE.md：「我們每一次改動，都是別人機器上的一次更新。」這個檔案守的是那條規則
裡「使用者連進不進得去都成問題」的那一種。

＊ 這個 app 沒有「忘記密碼」。

它沒有寄信的憑證要問使用者（那會是部署表單上的又一格空白），所以密碼只存在他自己
的腦袋和資料庫的那一欄雜湊裡。登入失敗＝**這份部署他再也進不去了**，而策略、通知
管道、持股全部在裡面。他唯一的出路是去他不熟的平台後台把資料庫清掉重來。

＊ 一行就做得到。

    _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

`schemes` 是一個**清單**，而 passlib 會拿它裡面的每一種去試著驗證。所以

    schemes=["argon2", "bcrypt"]     ← 安全，舊的照樣驗得過，新的用 argon2
    schemes=["argon2"]               ← 每一個現有使用者立刻進不去

兩行看起來一樣像「升級雜湊演算法」，而第二行不會有任何東西變紅：新註冊、新登入、
改密碼——所有測試都是在同一個行程裡先 hash 再 verify，兩邊會一起改。

**沒有任何一條測試記得上一版存進資料庫的長什麼樣子。** 這裡把它寫死。

＊ 這一條紅了要怎麼辦。

先假設不是這個檔案錯了。要換演算法的話，把新的排在清單**前面**、舊的留在後面
（`deprecated="auto"` 會在他下次登入成功時自動換成新格式），不要把舊的拿掉。
"""

from app.core.security import hash_password, verify_password

PASSWORD = "correct-horse-battery"

# 2026-09-04 由當時的 hash_password 產生的一筆，形狀就是資料庫 users.hashed_password
# 那一欄裡存的東西。值本身不重要，重要的是**以後的版本還讀得懂它**。
HASH_FROM_AN_EARLIER_VERSION = "$2b$12$WWBzyucf5fBljL075oKb7uYCXwFcD8.7mUrfiuVbknG2MWkEQVlrq"  # nosec B105 -- 測試用的固定樣本，不是憑證


def test_a_password_hashed_by_an_earlier_version_still_lets_him_in():
    """他三個月前設的密碼，今天更新完還要能登入。"""
    assert verify_password(PASSWORD, HASH_FROM_AN_EARLIER_VERSION) is True


def test_it_is_still_the_wrong_password_that_gets_refused():
    """上面那條單獨看，一個永遠回 True 的 verify_password 也會過。"""
    assert verify_password("not his password", HASH_FROM_AN_EARLIER_VERSION) is False


def test_what_this_version_writes_is_readable_by_this_version():
    """自我一致——真正的保證是上面那一條，這一條只是把兩半接起來。"""
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


def test_two_hashes_of_the_same_password_differ():
    """有加鹽。少了這一條，一個把 hash_password 改成回傳固定字串的改動會全綠。"""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)
