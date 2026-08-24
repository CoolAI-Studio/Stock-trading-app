"""What a fresh deployment still needs, in the language of the person filling it in.

WHY THIS EXISTS. The README hands a stranger two deploy buttons; render.yaml
then asks them for seven values, and two of those -- SECRET_ENCRYPTION_KEY and
the VAPID pair -- are produced by running a Python script on their own machine.
Somebody who wants stock alerts on their phone does not have Python. They leave
the blanks empty, the platform builds, the process dies at import
(config.enforce_required_secrets), and what they get is a 502 and a stack trace
in a log they will never find.

The one thing they need at that moment -- what is missing, why it matters, and
a button that produces the value -- is exactly what a dead process cannot serve.

THIS MODULE IS THE READ-ONLY HALF. It answers 「what is still missing」 and 「here
is a fresh value of the right shape」. It writes nothing, anywhere: the human
copies the value into the platform's own environment settings, which is the
only place that can persist it.

IT NEVER REPORTS A CONFIGURED VALUE. The endpoints that use it are
unauthenticated by necessity -- there is no login before there is a JWT_SECRET
-- so 「missing or present」 is the whole of what may leave this process. A
freshly generated random key gives an attacker nothing they could not have
generated themselves; an existing one hands them the deployment.

EVERY CHECK HERE DELEGATES TO config.py. If the two disagreed, the page would
report 「done」 about a boot that still crashes, which is worse than no page.
"""

import base64
import os
import secrets
from dataclasses import dataclass

from app.config import _PLACEHOLDER_SECRETS, LOCAL_BASE_URL, Settings
from app.services import hosting

# Long enough that guessing is not a strategy, and the same shape the existing
# hint in config.py tells people to generate by hand.
_TOKEN_BYTES = 48


@dataclass(frozen=True)
class SetupOption:
    """一個可以選的做法，附上選了它之後會怎樣。

    存在的理由是實測走過一遍才看得出來的：**雲端使用者做這個決定的地方只有登入
    之前那一頁。** 資料庫還是容器裡的檔案時，DATABASE_URL 擋住整個 app，他連帳
    號都還沒有，走不到登入之後的設定引導；等他走得到，資料庫已經是 Postgres 了。

    原本這裡是一段散文，一句話裡塞四個方案（「免費的例如 Neon、Supabase；要更穩
    的可以用付費方案，或是自己架的也行」）。讀它的人按這個專案的定義不是工程師，
    而一段話沒有辦法讓人「選」。
    """

    kind: str
    label: str
    detail: str
    url: str | None = None


@dataclass(frozen=True)
class MissingSetting:
    """One blank, and everything the person filling it in needs to know.

    `generator` names a value this app can produce for them -- the difference
    between a two-minute setup and an install of Python. None means only they
    can supply it, which for DATABASE_URL is the honest answer: it is somebody
    else's service and offering a button would be a lie.
    """

    name: str
    why: str
    how: str
    generator: str | None = None
    # False means the app boots and works, but something the owner expects to
    # work will not. Reported apart from the blocking ones because 「it will not
    # start」 and 「TradingView will send to the wrong address」 are not the same
    # urgency, and a page that mixes them teaches people to skim.
    blocking: bool = True
    # 可以選的做法。空的代表這一格沒有「選哪一種」的問題（一把金鑰就是一把金
    # 鑰），有東西的時候畫面要把它們並排攤開，而不是寫成一段話。
    options: tuple[SetupOption, ...] = ()
    # 這一格還要一起貼哪幾個環境變數。多數格子是空的——一個值就是一個值。
    #
    # 推播那一對不是：標題寫 VAPID_PUBLIC_KEY，內文說「兩個值都要貼回」，而照著
    # 標題走的人只會貼一個。只貼一半的下場是每一則推播都失敗，而畫面上沒有任何
    # 東西會說是因為少了另一半。
    also: tuple[str, ...] = ()
    # Which step of the deploy flow this belongs to. render.yaml presents seven
    # values as a flat parallel list; three of them are a chain, and a stranger
    # cannot see the chain. The number is what puts them back in order.
    step: int = 1


def _fernet_ok(key: str) -> bool:
    from cryptography.fernet import Fernet

    try:
        Fernet(key.strip().encode())
    except (ValueError, TypeError):
        return False
    return True


def _vapid_ok(s: Settings) -> tuple[bool, str | None]:
    """(ok, the half that is missing). Both empty is a valid deployment."""
    from app.config import _verify_vapid

    public = (s.VAPID_PUBLIC_KEY or "").strip()
    private = (s.VAPID_PRIVATE_KEY or "").strip()
    if not public and not private:
        # Web push is one channel of four. Somebody using only Telegram and
        # email must not be told their deployment is incomplete.
        return True, None
    if not public or not private:
        return False, "VAPID_PUBLIC_KEY" if not public else "VAPID_PRIVATE_KEY"
    try:
        _verify_vapid(s)
    except Exception:
        return False, "VAPID_PRIVATE_KEY"
    return True, None


def _database_unreachable() -> str | None:
    """開機時連不上資料庫的理由，或 None。

    READ FROM THE BOOT, NOT PROBED NOW. 第一版是在這裡真的去連一次，那有兩個問題
    而且第二個比較嚴重：每一次讀設定頁都要等一次連線逾時；而資料庫抖一下就會讓
    一份**已經設定好**的部署重新變成「未設定」——那會讓不需要登入的設定端點重新
    打開，也就是把一個暫時的故障變成一次資訊揭露。

    scripts/start.py 在開機跑遷移的時候已經知道答案了，而且它是唯一真正重要的
    那一刻：那時連不上，代表這份部署從來沒有起來過。它把（洗掉密碼的）理由放在
    這個環境變數裡。

    開機成功之後才壞掉的資料庫，是 /healthz 和系統狀態頁在管的事，不是設定頁。
    """
    return (os.environ.get("DATABASE_MIGRATION_ERROR") or "").strip() or None


def _database_options(on_a_platform: bool) -> tuple[SetupOption, ...]:
    """資料放哪裡，有哪幾條路。

    同一份清單在兩種環境下都給得出來，差別只在「跑在自己的機器上」那一條的後果：
    在自己的電腦上它是**做完了**，在雲端平台上它會被清空。兩種都要看得見——看不
    到的選項等於不存在，而這個決定是使用者的，不是這個 app 的。
    """
    return (
        SetupOption(
            kind="local",
            label="就跑在自己的電腦或自己的機器上",
            detail=(
                "這個平台每次重新部署都會換一個新的容器，裡面那個檔案會一起消失——"
                "帳號、策略、通知設定全部被清空，而且不會有任何提示。"
                "所以在這裡這不是一個能用的選擇。"
                if on_a_platform
                else "不用做任何事，資料就存在那個檔案裡。只要記得它是一個檔案："
                "跟其他重要檔案一起備份，這個系統不會替你備份它。"
            ),
        ),
        SetupOption(
            kind="cloud",
            label="Neon（免費方案夠用，不用信用卡）",
            detail="註冊之後開一個 project，它會給你一串 postgresql:// 開頭的連線字串。",
            url="https://neon.tech",
        ),
        SetupOption(
            kind="cloud",
            label="Supabase（免費方案，同樣是 Postgres）",
            detail="開一個 project，在 Project Settings → Database 裡複製連線字串。",
            url="https://supabase.com",
        ),
        SetupOption(
            kind="cloud",
            label="付費方案，或自己架的 Postgres",
            detail=(
                "免費方案通常會在閒置一段時間後休眠，醒來要等幾秒。"
                "在意這件事就用付費方案，或用你自己已經有的 Postgres——"
                "這個 app 不在乎是誰家的，它要的只是一串連線字串。"
            ),
        ),
    )


def missing_settings(s: Settings) -> list[MissingSetting]:
    """Everything that stops this deployment from working, most urgent first."""
    missing: list[MissingSetting] = []

    database_url = (s.DATABASE_URL or "").strip()
    # 同一個事實（一個檔案型資料庫）在兩個環境裡的後果不一樣，所以不能用同一句話
    # 講。認得出自己在哪一家平台上，這件事才分得開（services/hosting.py）。
    on_a_platform = hosting.detect() is not hosting.GENERIC
    a_file = database_url.startswith("sqlite")

    unreachable = _database_unreachable() if database_url else None

    if unreachable:
        # 「還沒填」和「填了但連不上」要做的事不一樣，所以不能共用一句話。
        # 這一格是整張表單上唯一一個 app 生不出來、只能去別人家的服務複製貼上的
        # 值，也就是最可能貼錯的那一格。
        missing.append(
            MissingSetting(
                name="DATABASE_URL",
                why=(
                    f"資料庫連不上。連線字串已經填了，但這個系統連不到它——對方回的是：{unreachable}"
                ),
                how=(
                    "多半是那一串本身有問題：貼的時候少了一段、密碼後來換過、"
                    "或那個資料庫已經被刪掉了。回你的資料庫服務主控台重新複製一次"
                    f"完整的連線字串，再貼進{hosting.paste_target()}的這一格。"
                ),
                generator=None,
                step=1,
            )
        )
    elif not database_url or (a_file and on_a_platform):
        missing.append(
            MissingSetting(
                name="DATABASE_URL",
                why=(
                    "沒有資料庫，這個系統存不了任何東西：帳號、策略、持倉、通知設定全部都"
                    "存不下來。目前用的是預設的本機檔案，而在雲端平台上那個檔案每次重新"
                    "部署都會被清空 —— 東西會不見，而且不會有任何提示。"
                ),
                how=(
                    # 「有哪幾條路」現在由下面的方案清單回答，一條一行、各自說出
                    # 後果。這裡只留機械動作——同一件事講兩次，讀的人會兩次都略過。
                    "下面選一個做法，拿到一串 postgresql:// 開頭的連線字串，"
                    f"貼進{hosting.paste_target()}的這一格。"
                ),
                generator=None,
                options=_database_options(on_a_platform=True),
                step=1,
            )
        )
    elif a_file:
        # 本機或自架：那個檔案就在他的硬碟上，不會因為誰重新部署而消失。把這個
        # 說成「還沒設定完」是錯的——app 會一直說他沒做完一件他已經決定好的事。
        #
        # 但也不能什麼都不說：它是一個檔案，可以被刪掉，而這裡沒有任何東西在
        # 替他備份。不擋，但講。
        missing.append(
            MissingSetting(
                name="DATABASE_URL",
                why=(
                    "你現在用的是本機的檔案資料庫（SQLite）。在自己的電腦或自己的機器上"
                    "這是一個正當的選擇 —— 那個檔案就在那裡，不會因為重新部署而消失。"
                ),
                how=(
                    "要繼續用本機檔案，不用做任何事。只要記得那是一個檔案："
                    "把它跟其他重要檔案一起備份，因為這個系統不會替你備份它。"
                    "之後想換成 Postgres 也隨時可以（免費的例如 Neon、Supabase，"
                    "自己架的也行），把連線字串放進 DATABASE_URL 就好。"
                ),
                generator=None,
                blocking=False,
                options=_database_options(on_a_platform=False),
                step=1,
            )
        )

    key = (s.SECRET_ENCRYPTION_KEY or "").strip()
    if not key or not _fernet_ok(key):
        missing.append(
            MissingSetting(
                name="SECRET_ENCRYPTION_KEY",
                why=(
                    "你的 Telegram 權杖、LINE 權杖、Email 密碼都是用這把金鑰加密後才存進"
                    "資料庫的。沒有它，這些東西一個都存不了。"
                    if not key
                    else "這個值的格式不對，會在你第一次要存通知設定的時候才出錯 —— "
                    "那時候你不會知道原因出在這裡。"
                ),
                how=(
                    f"按下面的「產生」，把產生出來的值貼回{hosting.paste_target()}。"
                    "不需要在自己電腦上裝任何東西。"
                ),
                generator="fernet",
                step=2,
            )
        )

    for name, why in (
        (
            "JWT_SECRET",
            "這是簽發登入憑證用的。沒有它或用了預設值，任何人都可以偽造成你的身分登入。",
        ),
        (
            "TV_WEBHOOK_SECRET",
            "這是 TradingView 送訊號進來時的通行碼。沒有它，任何人都可以假造訊號給你的系統。",
        ),
    ):
        value = (getattr(s, name) or "").strip()
        if not value or value in _PLACEHOLDER_SECRETS:
            missing.append(
                MissingSetting(
                    name=name,
                    why=why,
                    how=(
                        "有些平台（例如 Render）會自動幫你產生這一個。"
                        f"如果它是空的，按下面的「產生」，再把值貼回{hosting.paste_target()}。"
                    ),
                    generator="token",
                    step=2,
                )
            )

    ok, half = _vapid_ok(s)
    if ok and not (s.VAPID_PUBLIC_KEY or "").strip():
        # LISTED EVEN THOUGH IT IS NOT A FAULT. _vapid_ok calls a pair of empty
        # boxes a valid deployment, and it is right -- web push is one channel
        # of four, and somebody using only Telegram must not be told their
        # setup is incomplete.
        #
        # But 「not incomplete」 was implemented as 「not shown」, and so the row
        # never rendered and the button that generates the pair never appeared.
        # README tells the reader to leave both blank and press a button on the
        # next page. There was no button. This app is for somebody who wants
        # alerts on their phone; that button is the feature.
        #
        # Non-blocking, and the wording says so, so nothing calls the
        # deployment broken over it.
        missing.append(
            MissingSetting(
                name="VAPID_PUBLIC_KEY",
                why=(
                    "手機推播（瀏覽器通知）用的一對金鑰。現在是空的，所以手機收不到"
                    "推播 —— 其他通知管道（Email、Telegram）不受影響，"
                    "所以不用手機推播的話留白也可以。"
                ),
                how=(
                    "按下面的「產生」會一次給你完整的一對，兩個值都要貼回"
                    f"{hosting.paste_target()}："
                    "VAPID_PUBLIC_KEY 和 VAPID_PRIVATE_KEY。"
                    "另外 VAPID_SUBJECT 填你自己的信箱，格式是 mailto:you@example.com。"
                ),
                generator="vapid",
                blocking=False,
                also=("VAPID_PRIVATE_KEY",),
                step=3,
            )
        )
    if not ok:
        missing.append(
            MissingSetting(
                name=half or "VAPID_PRIVATE_KEY",
                why=(
                    "手機推播用的一對金鑰，現在只有一半、或兩半對不起來。這種狀態下每一則"
                    "推播都會被推播服務拒絕，而且畫面上看起來一切正常 —— 你會以為推播設好了，"
                    "實際上一則都收不到。"
                ),
                how=(
                    "按下面的「產生」會一次給你完整的一對，兩個值都要貼回"
                    f"{hosting.paste_target()}。"
                    "如果你不打算用手機推播，把兩個欄位都清空也是合法的設定。"
                ),
                generator="vapid",
                step=4,
            )
        )

    # --- not fatal, and the two a first-timer actually gets wrong ---------
    #
    # Neither stops the app booting, so neither was mentioned anywhere. They
    # are the tail of the chain render.yaml presents as a flat list: you cannot
    # know either URL until the thing it names exists.
    if s.public_base_url.strip() in ("", LOCAL_BASE_URL):
        missing.append(
            MissingSetting(
                name="PUBLIC_BASE_URL",
                why=(
                    "這是「你這台後端自己的網址」，只有一個地方用到：TradingView 設定頁"
                    "會告訴你要把哪個網址貼進 TradingView。現在它還是 localhost，"
                    "照著貼的話 TradingView 的訊號永遠不會送到，而且畫面上不會有任何提示。"
                ),
                how=(
                    "多數平台（Render、Railway、Koyeb、Fly.io）不用填 —— 系統會自動用"
                    "平台給這個服務的網址。只有在平台沒有給、或是你另外接了自訂網域的"
                    "時候才需要手動填；那種情況把完整網址（https:// 開頭）貼進來，"
                    "或是設一個叫 APP_PUBLIC_URL 的環境變數也可以。"
                ),
                generator=None,
                blocking=False,
                step=3,
            )
        )

    if not [origin for origin in s.cors_origins_list if not origin.startswith("http://localhost")]:
        missing.append(
            MissingSetting(
                name="CORS_ORIGINS",
                why=(
                    "這是「允許哪個網址來存取後端」。前端部署好之後如果沒有把它的網址填"
                    "進來，瀏覽器會把後端的每一個回應都丟掉 —— 你會看到一片空白的畫面，"
                    "而錯誤訊息藏在開發者工具裡，不會有人去看。"
                ),
                how=(
                    "等前端部署完（Vercel、Cloudflare Pages、Netlify 都可以），"
                    f"把它給你的網址（https:// 開頭）貼進{hosting.paste_target()}的這一格。"
                    "這一格一定是最後填的，因為在前端存在之前沒有人知道那個網址。"
                ),
                generator=None,
                blocking=False,
                step=5,
            )
        )

    missing.sort(key=lambda item: (not item.blocking, item.step))
    return missing


def blocking_settings(s: Settings) -> list[MissingSetting]:
    """Only the ones that stop the process from starting."""
    return [item for item in missing_settings(s) if item.blocking]


def is_configured(s: Settings) -> bool:
    """Whether the real app may serve. False means setup mode.

    Reads the BLOCKING list only: a deployment with the wrong CORS origin is
    misconfigured, not unstartable, and locking somebody out of an app that
    works would be a worse answer than letting them in to fix it.
    """
    return not blocking_settings(s)


def generate(kind: str) -> dict[str, str]:
    """A fresh value of the right shape. Never stored, here or anywhere.

    Each kind is produced by the same library the boot check validates with,
    so a generated value cannot be one the app then refuses to start on.
    """
    if kind == "fernet":
        from cryptography.fernet import Fernet

        return {"SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode()}

    if kind == "token":
        return {"value": secrets.token_urlsafe(_TOKEN_BYTES)}

    if kind == "vapid":
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        private = ec.generate_private_key(ec.SECP256R1())
        # The same encoding config._verify_vapid decodes: the raw 32-byte
        # scalar and an X9.62 uncompressed point, both base64url without
        # padding. Producing anything else would generate a pair this app
        # refuses to boot on.
        scalar = private.private_numbers().private_value.to_bytes(32, "big")
        point = private.public_key().public_bytes(
            encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
        )
        return {
            "VAPID_PRIVATE_KEY": base64.urlsafe_b64encode(scalar).rstrip(b"=").decode(),
            "VAPID_PUBLIC_KEY": base64.urlsafe_b64encode(point).rstrip(b"=").decode(),
        }

    # Not improvised. An unknown kind means the caller and this module disagree
    # about what exists, and inventing a value would ship a blank that fails at
    # boot with no clue where it came from.
    raise ValueError(f"不認識的產生類型：{kind}")
