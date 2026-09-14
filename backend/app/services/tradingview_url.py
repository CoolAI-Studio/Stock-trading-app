"""每個帳號自己的 TradingView 網址（#115）。

TradingView 的警報帶不了 bearer token，所以網址本身就是憑證：

    /api/webhooks/tradingview/{user_id}.{version}.{mac}
    mac = HMAC-SHA256(TV_WEBHOOK_SECRET, "tradingview-webhook-url:{user_id}:{version}")

**算出來的，不是存起來的**，而下面三條都是從這一點來的：

1. **讀它不需要資料庫。** 這支端點是公開的，任何人都打得到；一個要先查資料庫才知道真假
   的網址，等於讓陌生人用一個迴圈把 Neon 釘在醒著（#98 起的預算）。所以 `read` 只看字串
   和部署的密鑰，驗不過就結束。
2. **帳號是被證明的，不是被猜的。** 共用密碼只說得出「這是這個部署的某個人」，所以兩個帳
   號的時候只能拒絕（#24）。這裡 user_id 在 MAC 裡，改掉網址上的編號就驗不過。
3. **沒有新的秘密躺在資料庫裡。** 資料庫只有 `users.webhook_url_version` 一個整數；重新產
   生＝加一，舊網址立刻對不上。備份檔裡也因此沒有任何可以拿去送訊號的東西。

代價講明白：換掉 TV_WEBHOOK_SECRET 就是所有 TradingView 網址一起作廢——跟換掉共用密碼的意
思一樣，是刻意的（由 tests/test_a_tradingview_url_is_its_own_credential.py 釘著）。
"""

import base64
import hashlib
import hmac

_CONTEXT = "tradingview-webhook-url"

# 一個真的網址大約 50 個字元。上限只是防呆：不讓一條很長的路徑在驗證之前先花掉力氣。
_MAX_TOKEN_CHARS = 128


def _mac(user_id: int, version: int, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{_CONTEXT}:{user_id}:{version}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def token_for(user_id: int, version: int, secret: str) -> str:
    return f"{user_id}.{version}.{_mac(user_id, version, secret)}"


def personal_url(base_url: str, user_id: int, version: int, secret: str) -> str:
    return f"{base_url.rstrip('/')}/api/webhooks/tradingview/{token_for(user_id, version, secret)}"


def read(token: str, secret: str) -> tuple[int, int] | None:
    """(user_id, version)，或 None。**不碰資料庫**——版本號對不對是呼叫的人之後的事。

    只收 ASCII 數字：`str.isdigit()` 對「²」這種字元也說是，而 `int()` 會把某些全形數字
    默默轉成數字——同一個帳號就會有好幾種寫法都驗得過。
    """
    if not secret or len(token) > _MAX_TOKEN_CHARS:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_part, version_part, given = parts
    for part in (user_part, version_part):
        if not part or not part.isascii() or not part.isdigit():
            return None
    user_id, version = int(user_part), int(version_part)
    if user_id <= 0 or not given:
        return None
    expected = _mac(user_id, version, secret)
    if not hmac.compare_digest(expected.encode("ascii"), given.encode("utf-8")):
        return None
    return user_id, version
