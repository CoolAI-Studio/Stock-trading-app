"""部署表單上，他必須自己動手的格子有幾個。

＊ 為什麼這是一個要守的性質，而不是一個好聽的目標。

真的把這份東西交給一個目標使用者測試之後，回來的是「我不會用」。而流程停住的地方不
會出現在任何一份日誌裡——他只是關掉分頁。

現在還要他動手的其實只剩兩件事：

  一、DATABASE_URL。那是別人家服務上的東西，這個 app 生不出來，只能老實說（CLAUDE.md
      的使用者規則第一條）。
  二、SECRET_ENCRYPTION_KEY。**而這一格是我們自己造成的。**

第二格的流程是：部署 → 打開網址 → 設定頁按「產生」→ 複製 → 回到平台後台找到環境變數
那一頁 → 貼上 → 存檔 → 等它重新部署。七個動作，中間跨兩個網站，而任何一步中斷就前功
盡棄。

＊ 為什麼它以前非得這樣。

因為它必須是一把合法的 Fernet 金鑰（base64 的 32 bytes），而部署平台的「自動產生一個
隨機值」給的是普通的隨機字串——形狀不對，`Fernet()` 直接拒絕。

所以這個限制不是安全需求，是**格式需求**。而格式需求可以在我們這邊解決：接受任何夠長
的隨機值，自己把它推導成一把 Fernet 金鑰。金鑰仍然只活在環境變數裡（資料庫被整份倒出
去也拿不到它），而使用者一次都不用碰。

＊ 不可以退回去的那一條：舊的部署不能被這個改動弄壞。

已經在跑的那些實例，環境變數裡是一把真的 Fernet 金鑰，而資料庫裡有用它加密過的東西。
推導函式對那種值必須**原封不動地回傳**，否則他所有的通知設定和券商金鑰在下一次更新之
後全部解不開——而那是 #50 那條規則最嚴重的一種違反。
"""

import pytest

# 平台的「自動產生一個隨機值」長這樣：夠長的隨機字串，但不是 Fernet 的形狀。
PLATFORM_GENERATED = "kP3nZq7vXr2mTb9wLc5yHd8sJf4gNa6eRu1iOp0xQz"


def test_a_value_the_platform_generated_is_accepted(monkeypatch):
    """**這一條就是把那七個動作變成零個的那一條。**"""
    from app.config import fernet_key

    assert fernet_key(PLATFORM_GENERATED) is not None


def test_an_existing_fernet_key_is_returned_untouched():
    """已經在跑的實例不可以被這個改動弄壞（#50）。

    它們的環境變數裡是一把真的 Fernet 金鑰，而資料庫裡有用它加密過的東西。這裡如果
    「順手也推導一次」，那些東西在下一次更新之後就全部解不開了——而使用者不會知道原
    因，也沒有退路。
    """
    from cryptography.fernet import Fernet

    from app.config import fernet_key

    existing = Fernet.generate_key()

    assert fernet_key(existing.decode()) == existing


def test_data_encrypted_before_the_change_still_decrypts_after():
    """上一條的行為版本：真的加密一次，再用同一個值解開。

    比對回傳的 bytes 只能證明「值一樣」，證明不了「解得開」。而解不開才是使用者會遇
    到的那件事。
    """
    from cryptography.fernet import Fernet

    from app.config import fernet_key

    existing = Fernet.generate_key()
    sealed = Fernet(existing).encrypt(b"his telegram token")

    assert Fernet(fernet_key(existing.decode())).decrypt(sealed) == b"his telegram token"


def test_a_derived_key_is_stable_across_calls():
    """同一個環境變數每次都要推出同一把金鑰。

    不穩定的話症狀是最壞的一種：存得進去、下一次重啟之後解不開，而中間沒有任何錯誤。
    """
    from app.config import fernet_key

    assert fernet_key(PLATFORM_GENERATED) == fernet_key(PLATFORM_GENERATED)


@pytest.mark.parametrize("bad", ["", "   ", "short", "password123"])
def test_something_too_short_is_still_refused(bad):
    """接受任何隨機值，不等於接受任何值。

    推導不會憑空生出亂度：一個猜得到的字串推出來的金鑰也是猜得到的。太短就是沒設好，
    而沒設好要說出口。
    """
    from app.config import fernet_key

    assert fernet_key(bad) is None


def test_the_deploy_form_no_longer_asks_him_for_it():
    """render.yaml 要讓平台自己產生這一格。

    上面那些都做到了，而這一行沒改的話，他在部署表單上看到的還是一個空格。
    """
    from pathlib import Path

    render = (Path(__file__).resolve().parent.parent.parent / "render.yaml").read_text(
        encoding="utf-8"
    )
    block = render.split("- key: SECRET_ENCRYPTION_KEY", 1)[1].split("- key:", 1)[0]

    assert "generateValue: true" in block, "部署表單還在要他自己填加密金鑰"


def test_the_setup_page_does_not_report_a_generated_value_as_missing():
    """設定頁跟 config.py 不可以有兩套判準。

    不同調的話，畫面會說「你還缺這個」而其實已經好了——他會照著去產生一把新的，貼上
    去，然後**所有已經存起來的秘密都解不開了**。
    """
    from app.config import Settings
    from app.services import setup_state

    s = Settings(
        DATABASE_URL="sqlite://",
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        SECRET_ENCRYPTION_KEY=PLATFORM_GENERATED,
    )

    assert "SECRET_ENCRYPTION_KEY" not in [i.name for i in setup_state.missing_settings(s)]
