"""每個帳號自己的 TradingView 網址（#115）。

TradingView 的警報帶不了 bearer token，所以網址本身就是憑證：

    /api/webhooks/tradingview/{user_id}.{version}.{mac}
    key = HMAC-SHA256(SECRET_ENCRYPTION_KEY, "stock-alerts/tradingview-webhook-url/v1")
    mac = HMAC-SHA256(key, "tradingview-webhook-url:{user_id}:{version}")

**算出來的，不是存起來的**，而下面四條都是從這一點來的：

1. **讀它不需要資料庫。** 這支端點是公開的，任何人都打得到；一個要先查資料庫才知道真假
   的網址，等於讓陌生人用一個迴圈把 Neon 釘在醒著（#98 起的預算）。所以 `read` 只看字串
   和部署的密鑰，驗不過就結束。
2. **帳號是被證明的，不是被猜的。** 共用密碼只說得出「這是這個部署的某個人」，所以兩個帳
   號的時候只能拒絕（#24）。這裡 user_id 在 MAC 裡，改掉網址上的編號就驗不過。
3. **沒有新的秘密躺在資料庫裡。** 資料庫只有 `users.webhook_url_version` 一個整數；重新產
   生＝加一，舊網址立刻對不上。備份檔裡也因此沒有任何可以拿去送訊號的東西。
4. **金鑰是一個從來不離開伺服器的秘密。** 第一版用的是 TV_WEBHOOK_SECRET，而那一個會明文
   寫在每一則舊警報的「訊息」裡——跟著 TradingView 的彈窗、通知信、手機通知、截圖一起流
   出去。拿到它的人算得出任何帳號、任何版本的網址，「重新產生」擋不住（一次唯讀審查抓到
   的）。SECRET_ENCRYPTION_KEY 只活在環境變數裡、不出現在任何一個請求裡，而且本來就必須
   固定不變：換掉它等於丟掉所有加密過的通知設定，推播金鑰也是從它推導的（config.vapid_keys）。
   鹽不一樣，所以這裡推出來的金鑰跟那兩把沒有關係。

代價講明白：換掉 SECRET_ENCRYPTION_KEY 就是所有 TradingView 網址一起作廢。換掉
TV_WEBHOOK_SECRET 則只影響舊的共用網址——那正是共用密碼外洩時的補救，不會連帶弄斷他已經換
好的專屬網址。兩條都由 tests/test_a_tradingview_url_is_its_own_credential.py 釘著。
"""

import base64
import hashlib
import hmac
from functools import lru_cache

_CONTEXT = "tradingview-webhook-url"

# 固定的鹽，作用是領域分隔：同一個 SECRET_ENCRYPTION_KEY 推出來的金鑰，不會跟加密或推播用的那
# 幾把一樣。它必須固定——換掉它等於讓每一條已經貼進 TradingView 的網址一起失效。
_KEY_LABEL = b"stock-alerts/tradingview-webhook-url/v1"

# 一個真的網址大約 50 個字元。上限只是防呆：不讓一條很長的路徑在驗證之前先花掉力氣。
_MAX_TOKEN_CHARS = 128


@lru_cache(maxsize=4)
def _signing_key(server_secret: str) -> bytes:
    return hmac.new(server_secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()


def _mac(user_id: int, version: int, server_secret: str) -> str:
    digest = hmac.new(
        _signing_key(server_secret),
        f"{_CONTEXT}:{user_id}:{version}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def token_for(user_id: int, version: int, server_secret: str) -> str:
    if not server_secret:
        # 沒有金鑰就不發網址。空字串推得出一把「金鑰」，而那一把人人都算得出來。
        raise ValueError("a personal TradingView URL needs the deployment's server secret")
    return f"{user_id}.{version}.{_mac(user_id, version, server_secret)}"


def personal_url(base_url: str, user_id: int, version: int, server_secret: str) -> str:
    token = token_for(user_id, version, server_secret)
    return f"{base_url.rstrip('/')}/api/webhooks/tradingview/{token}"


def read(token: str, server_secret: str) -> tuple[int, int] | None:
    """(user_id, version)，或 None。**不碰資料庫**——版本號對不對是呼叫的人之後的事。

    `server_secret` 是 SECRET_ENCRYPTION_KEY，不是 TV_WEBHOOK_SECRET（理由在檔頭第 4 條）。

    只收 ASCII 數字：`str.isdigit()` 對「²」這種字元也說是，而 `int()` 會把某些全形數字
    默默轉成數字——同一個帳號就會有好幾種寫法都驗得過。
    """
    if not server_secret or len(token) > _MAX_TOKEN_CHARS:
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
    expected = _mac(user_id, version, server_secret)
    if not hmac.compare_digest(expected.encode("ascii"), given.encode("utf-8")):
        return None
    return user_id, version
