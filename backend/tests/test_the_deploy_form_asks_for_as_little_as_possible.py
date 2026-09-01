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


# --- 推播那三格 ---------------------------------------------------------------
#
# 這個產品的一句話是「想在手機上收到股票提醒」，而 iOS 的 Web Push 只在「加到主畫面
# 再從那裡打開」的網站上能用（見 frontend/src/lib/platform.ts）。所以推播不是加分項，
# 它幾乎就是這個產品本身。
#
# 但它要一對 P-256 金鑰，而部署平台生不出那種形狀。以前的說法是「兩個都留空也是合法
# 設定」——技術上對，實際上那句話的意思是：**照著最短路徑走完的人，永遠收不到手機通
# 知，而且沒有任何東西會說。**
#
# 那對金鑰其實不需要另外存：私鑰就是 32 bytes 的純量，而這一份部署已經有一個高亂度、
# 只活在環境變數裡、而且**本來就必須固定不變**的秘密（換掉 SECRET_ENCRYPTION_KEY 等
# 於丟掉所有加密過的資料）。用不同的 salt 推導出來，就有了一對穩定的金鑰，不用開新
# 資料表、不用遷移、不用多存一個秘密。
#
# **只在兩個都沒設的時候才推導。** 這一條讓這個改動對已經在跑的部署零風險：有設的照
# 用自己那一對；沒設的本來就沒有推播，也就沒有任何訂閱會被弄壞。


def _settings_with(**kw):
    from app.config import Settings

    base = dict(
        DATABASE_URL="sqlite://",
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        SECRET_ENCRYPTION_KEY=PLATFORM_GENERATED,
    )
    base.update(kw)
    return Settings(**base)


def test_a_deployment_that_set_nothing_still_gets_a_usable_pair():
    """**這一條就是「照最短路徑走完也收得到手機通知」。**"""
    from app.config import vapid_keys

    public, private = vapid_keys(_settings_with())

    assert public and private


def test_the_derived_pair_really_is_a_pair():
    """公鑰要真的是那把私鑰算出來的。

    不成對的後果是這個 repo 已經寫下來的那一種：Apple 對每一次推播回 403
    VapidPkHashMismatch，而 app 開機全綠、健康檢查全綠、頻道建得起來還回報成功——
    一個提醒都不會到。
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from app.config import _b64url, vapid_keys

    public, private = vapid_keys(_settings_with())
    scalar = int.from_bytes(_b64url(private), "big")
    expected = (
        ec.derive_private_key(scalar, ec.SECP256R1())
        .public_key()
        .public_bytes(encoding=Encoding.X962, format=PublicFormat.UncompressedPoint)
    )

    assert _b64url(public) == expected


def test_the_pair_is_the_same_after_a_restart():
    """換一對金鑰會讓每一台已經訂閱的裝置失效，而使用者不會知道要重新設定。"""
    from app.config import vapid_keys

    assert vapid_keys(_settings_with()) == vapid_keys(_settings_with())


def test_a_configured_pair_wins():
    """**這一條讓這個改動對已經在跑的部署零風險。**

    他們環境變數裡有一對能用的金鑰，而手機上有依那把公鑰建立的訂閱。這裡如果改成用推
    導的，那些訂閱全部失效——而症狀是靜默的：推播照送、回 403、沒有人看得到。
    """
    from app.config import vapid_keys

    mine = _settings_with(VAPID_PUBLIC_KEY="BPublicHalf", VAPID_PRIVATE_KEY="cHJpdmF0ZQ")

    assert vapid_keys(mine) == ("BPublicHalf", "cHJpdmF0ZQ")


def test_without_an_encryption_key_there_is_nothing_to_derive_from():
    """推導不會憑空生出亂度。沒有來源就是沒有推播，而那要說得出口，不是假裝有。"""
    from app.config import vapid_keys

    assert vapid_keys(_settings_with(SECRET_ENCRYPTION_KEY="")) == (None, None)


def test_the_subject_falls_back_to_this_deployment_itself():
    """VAPID_SUBJECT 是「推播服務要找誰」，RFC 8292 允許 mailto: 或 https:。

    預設值 mailto:admin@example.com 是**別人的信箱**，而這個 app 知道自己的網址——那
    是一個真的、而且不需要跟使用者要的答案。順帶也不用把他的信箱送給 Apple 和 Google。
    """
    from app.config import vapid_subject

    s = _settings_with(PUBLIC_BASE_URL="https://his-own-deployment.onrender.com")

    assert vapid_subject(s) == "https://his-own-deployment.onrender.com"


def test_a_subject_he_actually_set_is_kept():
    from app.config import vapid_subject

    assert vapid_subject(_settings_with(VAPID_SUBJECT="mailto:him@his-domain.com")) == (
        "mailto:him@his-domain.com"
    )


def test_the_deploy_form_no_longer_asks_for_the_push_keys():
    from pathlib import Path

    render = (Path(__file__).resolve().parent.parent.parent / "render.yaml").read_text(
        encoding="utf-8"
    )

    for key in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        assert f"- key: {key}" not in render, f"部署表單還在問他 {key}"


def test_the_setup_page_does_not_list_push_keys_it_can_derive():
    from app.services import setup_state

    names = [i.name for i in setup_state.missing_settings(_settings_with())]

    assert "VAPID_PUBLIC_KEY" not in names
    assert "VAPID_PRIVATE_KEY" not in names


def test_when_there_is_nothing_to_derive_from_it_is_still_listed():
    """推不出來的時候，那一列要回來。

    這是上一條（推得出來就不列）的另一半。少了它，一個連加密金鑰都沒有的部署會**安靜
    地**沒有手機推播——而這個 app 就是為了手機通知存在的。
    """
    from app.services import setup_state

    names = [i.name for i in setup_state.missing_settings(_settings_with(SECRET_ENCRYPTION_KEY=""))]

    assert "VAPID_PUBLIC_KEY" in names


def test_it_says_not_to_build_anything_until_the_database_is_real(monkeypatch):
    """**先部署、之後再弄資料庫**這條路能走，但它有一個陷阱。

    `DATABASE_URL` 有預設值，所以留空也部署得起來——畫面會起來，設定頁會帶他去弄資料
    庫。那對一個不確定要不要再註冊一家服務的人是好事：他可以先看到東西動起來。

    但那時候用的是容器裡的一個檔案，而它每次重新部署就消失。他如果先建了帳號、寫了
    策略、設好通知，等到把 DATABASE_URL 填進去，**那些全部不見**——換成一個全新的空資
    料庫。而他不會預期這件事，因為畫面從頭到尾看起來都正常。

    設定頁已經說了「那個檔案會被清空」，但沒有說**因此現在先不要建東西**。差別在於前
    者描述狀態，後者告訴他此刻該做什麼——而他需要的是後者。
    """
    from app.config import Settings
    from app.services import setup_state

    # 平台的判斷看的是 RENDER 這個標記（hosting._KNOWN），不是那個網址。
    monkeypatch.setenv("RENDER", "true")
    s = Settings(
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        SECRET_ENCRYPTION_KEY=PLATFORM_GENERATED,
        DATABASE_URL="sqlite:///./trading_app_dev.db",
    )

    entry = next(i for i in setup_state.missing_settings(s) if i.name == "DATABASE_URL")

    assert "建立帳號" in entry.how or "先不要" in entry.how


@pytest.mark.parametrize("marker", ["**", "__", "`"])
def test_no_setting_text_contains_markdown(monkeypatch, marker):
    """設定頁的文案是**純文字**渲染的（SetupPage 的 `{item.how}`），所以 markdown 的
    記號會原樣出現在畫面上。

    這一條是補的：我在 DATABASE_URL 的說明裡寫了 `**…**`，而 first-run 那一關（真的
    開瀏覽器走一遍全新部署）抓到了它——`畫面上沒有沒被渲染的 markdown`。那一關很有價
    值，但它要跑一個真的容器加一個真的瀏覽器，而這件事一個字串比對就問得出來。

    涵蓋所有欄位，不只我改到的那一個：下一個人寫的時候不會記得這條規則。
    """
    from app.config import Settings
    from app.services import setup_state

    monkeypatch.setenv("RENDER", "true")
    for s in (
        Settings(JWT_SECRET="", TV_WEBHOOK_SECRET="", SECRET_ENCRYPTION_KEY="", DATABASE_URL=""),
        Settings(
            JWT_SECRET="a-real-secret-value-not-a-placeholder",
            TV_WEBHOOK_SECRET="another-real-secret-value-here",
            SECRET_ENCRYPTION_KEY=PLATFORM_GENERATED,
            DATABASE_URL="sqlite:///./trading_app_dev.db",
        ),
    ):
        for item in setup_state.missing_settings(s):
            fields = [("why", item.why), ("how", item.how)]
            # **選項也要掃。** 第一版只掃 why 和 how，於是我隔一輪就在一個 option 的
            # detail 裡又寫了 `**有期限**`，而 first-run 那一關（真瀏覽器）又抓一次。
            # 一份漏掉一半的清單，比沒有清單更糟：它會讓人以為已經檢查過了。
            for option in item.options or ():
                fields.append((f"option[{option.label}].label", option.label))
                fields.append((f"option[{option.label}].detail", option.detail))
            for field, text in fields:
                assert marker not in text, f"{item.name} 的 {field} 裡有 markdown：{marker}"


def test_it_no_longer_asks_him_for_the_push_contact_address(monkeypatch):
    """VAPID_SUBJECT 現在是自動的，所以不可以再跟他要。

    那一格是「推播服務要找誰」，RFC 8292 允許 https:，而 config.vapid_subject 會用這份
    部署自己的網址。跟他要一個 app 已經有的值，是這個 repo 的使用者規則第一條在講的那
    件事——而且他照著填一個信箱，換來的是把自己的信箱送給 Apple 和 Google。

    這一條是**我自己改動留下的尾巴**：我把 subject 改成自動的，卻沒有回頭改那句叫他填
    信箱的說明。
    """
    from app.config import Settings
    from app.services import setup_state

    monkeypatch.setenv("RENDER", "true")
    s = Settings(
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        SECRET_ENCRYPTION_KEY="",  # 推不出來，所以那一列會出現
        DATABASE_URL="postgresql://u:p@example.com/db",
    )

    for item in setup_state.missing_settings(s):
        assert "VAPID_SUBJECT" not in item.how, f"{item.name} 還在叫他填 VAPID_SUBJECT"


def test_the_platform_own_database_is_offered_as_a_choice_with_its_cost():
    """**選項要列出來，代價也要，然後由他決定。**

    `DATABASE_URL` 是部署流程裡唯一還要他動手的一格，而它其實拿得掉：部署平台多半有自
    己的 Postgres，`render.yaml` 宣告一下就會自動接上，一格都不用填。

    我一直建議不要那樣做，因為那些免費資料庫有期限，而到期是**刪除**——某一天他的策
    略、通知設定、帳號全部消失，事前不會知道。但「我建議不要」跟「他看不到這個選項」
    是兩件事，而這個 app 的做法一向是後者不可以：`_database_options` 的檔頭本來就寫著
    「看不到的選項等於不存在，而這個決定是使用者的，不是這個 app 的」。

    所以列出來，並且把代價寫在同一行——跟「跑在自己機器上」那一條的處理方式一樣。
    """
    from app.services import setup_state

    options = setup_state._database_options(on_a_platform=True)
    platform = [o for o in options if "平台" in o.label or "Render" in o.label]

    assert platform, "沒有列出「用部署平台自己的資料庫」這個選項"
    detail = platform[0].detail
    assert "到期" in detail or "期限" in detail, "列了選項卻沒說它會到期"
    assert "刪" in detail, "沒說到期之後資料是被刪掉，不是停用"


# --- DEPLOYMENT.md 也要跟著事實走 ---------------------------------------------
#
# **沒有任何測試在讀這個檔案**，所以它變舊不會讓任何東西變紅——而 README:50
# 「完整說明見 DEPLOYMENT.md」正是把使用者送過去的那一行。
#
# 它落後的後果不是「文件不準」那麼輕：§6 教的舊 VAPID 流程如果照做，會用一對新金鑰
# 蓋掉推導值，讓**每一台已經訂閱的手機靜默收不到推播**（config.py 自己寫著
# 「changing the pair invalidates every existing subscription」）。


def _deployment_md() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent / "DEPLOYMENT.md").read_text(
        encoding="utf-8"
    )


def test_it_does_not_tell_him_to_paste_a_vapid_pair_back():
    """照著做會讓已經訂閱的每一台裝置失效，而且那顆按鈕根本不會出現。

    推播金鑰現在從 SECRET_ENCRYPTION_KEY 推導（兩格都空才推），而設定頁那一列的條件
    是 `not push_works`——正常部署上推得出來，所以那一列不出現、那顆「產生」按鈕也不
    存在。這份文件同時在叫他去按一顆不存在的按鈕，以及去做一件會弄壞推播的事。
    """
    text = _deployment_md()

    assert "把兩個值貼回你的部署平台的環境變數" not in text
    assert "VAPID_SUBJECT` 填你自己的聯絡信箱" not in text


def test_it_does_not_send_him_to_install_postgres_just_to_back_up():
    """app 自己就會定期把加密備份寄到他的信箱（backup_schedule.py）。

    而這一節叫一個不是工程師的人去下載安裝 PostgreSQL Command Line Tools、打開終端機
    貼一行 pg_dump，而且把那件事列進檢查清單。CLAUDE.md：「任何『請在你的電腦上跑這
    支腳本』的指示，對這個使用者等於流程到此結束。」

    pg_dump 那條路沒有被拿掉——它是完整的資料庫傾印，對想要那個的人是對的。改的是
    **順序**：先講 app 自己會做的那個。
    """
    text = _deployment_md()

    # 比對的是**那件事被提到**，不是某一句特定的話：文案會改，而規則是「app 自己會做
    # 的事要先講」。第一版比對「寄到你的信箱」而我寫的是「寄到你的 Email」，於是它對
    # 著一份已經改好的文件報錯。
    assert "排程" in text and "備份密碼" in text, "備份那一節沒有提到 app 內建的排程備份"
    assert text.index("排程") < text.index("pg_dump"), (
        "先教他裝 PostgreSQL，才提 app 自己就會做的那件事"
    )
