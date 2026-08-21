"""What a fresh deployment still needs, in the language of the person filling it in.

WHY THIS EXISTS. The README hands a stranger two deploy buttons; render.yaml
then asks them for seven values, and two of those -- SECRET_ENCRYPTION_KEY and
the VAPID pair -- are produced by running a Python script on their own machine.
Somebody who wants stock alerts on their phone does not have Python. They leave
the blanks empty, Render builds, the process dies at import
(config.enforce_required_secrets), and what they get is a 502 and a stack trace
in a log they will never find.

The one thing they need at that moment -- what is missing, why it matters, and
a button that produces the value -- is exactly what a dead process cannot serve.

THIS MODULE IS THE READ-ONLY HALF. It answers 「what is still missing」 and 「here
is a fresh value of the right shape」. It writes nothing, anywhere: the human
copies the value into Render, which is the only place that can persist it.

IT NEVER REPORTS A CONFIGURED VALUE. The endpoints that use it are
unauthenticated by necessity -- there is no login before there is a JWT_SECRET
-- so 「missing or present」 is the whole of what may leave this process. A
freshly generated random key gives an attacker nothing they could not have
generated themselves; an existing one hands them the deployment.

EVERY CHECK HERE DELEGATES TO config.py. If the two disagreed, the page would
report 「done」 about a boot that still crashes, which is worse than no page.
"""

import base64
import secrets
from dataclasses import dataclass

from app.config import _PLACEHOLDER_SECRETS, Settings

# Long enough that guessing is not a strategy, and the same shape the existing
# hint in config.py tells people to generate by hand.
_TOKEN_BYTES = 48


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


def missing_settings(s: Settings) -> list[MissingSetting]:
    """Everything that stops this deployment from working, most urgent first."""
    missing: list[MissingSetting] = []

    if not (s.DATABASE_URL or "").strip() or s.DATABASE_URL.startswith("sqlite"):
        missing.append(
            MissingSetting(
                name="DATABASE_URL",
                why=(
                    "沒有資料庫，這個系統存不了任何東西：帳號、策略、持倉、通知設定全部都"
                    "存不下來。目前用的是預設的本機檔案，而在 Render 上那個檔案每次重新"
                    "部署都會被清空 —— 東西會不見，而且不會有任何提示。"
                ),
                how=(
                    "去 neon.tech 註冊一個免費帳號、建立一個資料庫，把它給你的連線字串"
                    "（postgresql://... 開頭）貼進 Render 的這個欄位。免費方案就夠用。"
                ),
                generator=None,
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
                how="按下面的「產生」，把產生出來的值貼回 Render。不需要在自己電腦上裝任何東西。",
                generator="fernet",
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
                        "Render 通常會自動幫你產生這一個。如果它是空的，按下面的「產生」再貼回去。"
                    ),
                    generator="token",
                )
            )

    ok, half = _vapid_ok(s)
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
                    "按下面的「產生」會一次給你完整的一對，兩個值都要貼回 Render。"
                    "如果你不打算用手機推播，把兩個欄位都清空也是合法的設定。"
                ),
                generator="vapid",
            )
        )

    return missing


def is_configured(s: Settings) -> bool:
    """Whether the real app may serve. False means setup mode."""
    return not missing_settings(s)


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
