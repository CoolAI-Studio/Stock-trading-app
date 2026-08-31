import base64
import logging
import sys
from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic_settings import BaseSettings, SettingsConfigDict

# The PUBLIC_BASE_URL default, named so the 「still unset」 check in
# services/setup_state.py and the value that ships cannot drift apart. Module
# level rather than a class attribute: pydantic turns a leading-underscore
# class attribute into a ModelPrivateAttr, which compares equal to nothing and
# made the check silently never fire.
LOCAL_BASE_URL = "http://localhost:8000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite:///./trading_app_dev.db"

    JWT_SECRET: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    SECRET_ENCRYPTION_KEY: str = ""

    # Web Push (browser notifications). Generate with:
    #   python -c "from py_vapid import Vapid02; v=Vapid02(); v.generate_keys(); ..."
    # (see scripts/generate_vapid_keys.py). Public key is safe to expose to the
    # frontend; private key must stay server-side only.
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:admin@example.com"

    TV_WEBHOOK_SECRET: str = "change-me"

    CORS_ORIGINS: str = "http://localhost:5173"

    AI_PROVIDER: str = "openai_compatible"
    AI_BASE_URL: str = "https://openrouter.ai/api/v1"
    AI_API_KEY: str = ""
    AI_MODEL: str = ""

    MARKET_DATA_POLL_INTERVAL_SEC: float = 5.0

    # Wall-clock budget for one strategy on_tick() call. Kept well under
    # MARKET_DATA_POLL_INTERVAL_SEC so that even several misbehaving strategies
    # can't push a single poll cycle past its own interval. A real strategy is
    # arithmetic over a price list -- microseconds -- so 2s only ever fires on
    # an infinite loop or a hung network call.
    STRATEGY_TICK_TIMEOUT_SEC: float = 2.0

    # 驗證一支策略的期限：編譯 ＋ 拿十個樣本價試跑，全部在一次往返裡。
    #
    # 比 tick 的期限寬，因為它做的事比較多（compile 本身就要執行類別主體和
    # __init__）；但仍然是個小數字，因為另一端是一個等著看結果的人。在搬進子行程
    # 之前這裡**沒有任何期限**——_guarded 只包 on_tick / on_bar，不包建構式，所以
    # 一支在 __init__ 裡 while True 的策略會讓那個請求執行緒永遠回不來，而
    # /api/strategies/validate 只需要一個登入。
    STRATEGY_VALIDATE_TIMEOUT_SEC: float = 5.0

    # 一場回測的**總**期限。
    #
    # 在搬進子行程之前，這裡只有「每一根 K 棒兩秒」，沒有總額——五千根就是兩小時
    # 多的請求執行緒，外加五千條殺不掉的執行緒（Python 殺不掉執行緒，見
    # strategy_runtime._guarded 的檔頭）。而回測是使用者最常按的按鈕，按下去跑的
    # 是自己寫的、還沒驗過的程式碼。
    #
    # 三十秒：一場正常的回測是幾千次算術，幾十毫秒的事；這個數字只會在跑不完的
    # 東西上生效。
    STRATEGY_BACKTEST_TIMEOUT_SEC: float = 30.0

    # 一個策略子行程最多能配多少記憶體。
    #
    # 逾時擋的是時間，這一項擋的是空間，而空間的失效模式不會逾時：
    #
    #     def on_bar(self, bar):
    #         self.junk = [0] * 10**10
    #
    # 這一行跑得很快，只是要一百 GB。沒有上限的話，作業系統開始換頁，然後 API、
    # 盯盤迴圈和通知一起停下來——而警告不能停擺是這個產品的最高優先。
    #
    # 256 MB：目標機器（Render 免費方案）總共只有 512 MB，而一支策略是價格清單上
    # 的算術。真正的策略用不到這個數字的百分之一；會撞到它的，是本來就該被擋下來
    # 的那種。**只有 POSIX 有效**（Windows 沒有 resource 模組），而線上是 Linux。
    STRATEGY_MEMORY_LIMIT_MB: int = 256

    # 去哪裡問「有沒有新版」。空字串代表關掉。
    #
    # 有預設值，所以它不是部署表單上的一格空白（#51 的規則）。問的是 `stable` 分
    # 支——那是 CI 全綠、部署送達、而且線上健康之後才前進的那條線。
    UPDATE_CHECK_REPO: str = "CoolAI-Studio/Stock-trading-app"

    # 每一支策略最多留幾個版本。
    #
    # 有上限是因為免費方案的資料庫塞得爆，而策略是文字：二十版 × 幾 KB × 幾支策
    # 略，量很小，但沒有上限的東西遲早會變大。丟最舊的，而**現在在跑的那一版永遠
    # 不丟**——那是他唯一真正需要的一版。
    STRATEGY_VERSION_LIMIT: int = 20

    WS_TICKET_TTL_SECONDS: int = 30

    # Closed by default: with registration off, a publicly reachable backend only
    # exposes login + the secret-gated TradingView webhook. First user is created
    # via scripts/create_user.py.
    # No longer the thing that guards the door: registration closes itself once
    # the deployment has an owner (see routers/auth.py). Kept only so a
    # deployment that wants its account made by hand can refuse even the first
    # one.
    ALLOW_REGISTRATION: bool = False

    # Whether a brand-new deployment may create its FIRST account from the web
    # page. On by default, because that is the whole 「按一個按鈕部署自己一份」
    # flow -- the alternative was DEPLOYMENT.md's three-step curl dance, which
    # for this audience is the same as no flow at all. It stops mattering the
    # moment that first account exists.
    ALLOW_FIRST_ACCOUNT: bool = True

    # FastAPI's /docs, /redoc and /openapi.json. OFF by default, and the
    # default is the one that matters: nobody deploying a copy of this will
    # ever set this, and they should not have to think about it.
    #
    # MEASURED on the live deployment: both /docs and /openapi.json answered
    # 200 to anybody. No user data is in the schema, so this was never a leak
    # -- but it is a complete map of 82 operations handed to anyone who knows
    # the backend's address, and that address travels in every request the
    # frontend makes. The owner of a deployment is not an engineer and will
    # never open /docs, so its only readers were people looking for a way in.
    #
    # Deliberately NOT declared in render.yaml: it is for local development,
    # and every value on that form is a place a first-time deployer stops.
    ENABLE_API_DOCS: bool = False

    # Brute-force throttle on POST /api/auth/login: one password guards every
    # stored broker credential, so a public URL must not allow unlimited guesses.
    # Counters live in-process (app/core/login_throttle.py).
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: float = 15.0

    # Escape hatch for verify_required_secrets() below -- never set it in a
    # deployment. See the comment there for why it exists.
    ALLOW_INSECURE_SECRETS: bool = False

    # Must run with --workers 1 when true: two worker processes would each run
    # their own polling loop and duplicate signals for the same tick. Tests
    # force this off (see tests/conftest.py) so the suite never spins up a
    # real polling loop or touches the network.
    WORKER_ENABLED: bool = True

    # 一輪 tick 的合理上限。裡面最慢的是通知重送掃描，它自己有 20 秒的預算
    # （notification/retry.py::_MAX_SWEEP_SEC），抓報價和 K 棒還要再加上去。
    HEALTH_TICK_BUDGET_SEC: float = 60.0

    # /healthz fails (503) when the worker's last loop iteration, or its last
    # poll cycle that completed, is older than this.
    #
    # IT MUST CLEAR THE LONGEST SLEEP, PLUS ONE TICK. 這個值原本是 300，而市場
    # 關著的時候 next_poll_delay() 回的 market_loop.CLOSED_POLL_INTERVAL_SEC
    # 也正好是 300。run_forever 的順序是「mark_loop → 跑一輪 tick（耗時 T）→
    # 睡 300 秒」，所以每一個循環都有長度 T 的一段時間，探測打進來就是 fail。
    # 台股使用者從 13:30 到隔天 09:00 都在這個狀態：每天半夜一封「worker 沒有
    # 在跑」，而 worker 好得很。
    #
    # 一個每天亂叫的警報器會被學會忽略，然後真的停擺那一次的信長得一模一樣。
    # tests/test_the_watchdog_does_not_cry_wolf.py 把這個關係釘成不變式：
    # 任何一邊的間隔被調整，那些測試就會紅。
    HEALTH_MAX_AGE_SEC: float = 420.0

    # Render's free tier spins the whole process down when idle, so the first
    # probe after a cold start meets a worker that truthfully has never polled.
    # Inside this window that reports as "starting" instead of failing.
    HEALTH_STARTUP_GRACE_SEC: float = 120.0
    # Polls in a row that fetched nothing before /healthz calls the feed dead.
    # At the default 15s interval this is about five minutes of total silence
    # -- long enough that a single provider hiccup does not page anyone, short
    # enough that a blocked IP is caught the same afternoon rather than
    # whenever the owner next notices no orders have appeared.
    HEALTH_MAX_EMPTY_POLLS: int = 20

    # How long ONE symbol may go without a price before /healthz calls it
    # dead. The empty-poll count above cannot see this: it clears on any one
    # good price, so nine working symbols hide a tenth that never resolves --
    # and every alert on that tenth has silently stopped.
    #
    # Well above the few minutes a quote is legitimately served from cache
    # while refreshes fail (see _DEFAULT_STALE_LIMIT_SEC), so the two never
    # argue, and above a market's normal quiet: a closed exchange still
    # answers with its last close, so silence here means the symbol cannot be
    # resolved at all rather than that nothing is trading.
    HEALTH_MAX_SYMBOL_GAP_SEC: float = 900.0

    # This deployment's own public address, used to tell the owner what URL to
    # paste into TradingView. Not derivable from a request: the app sits
    # behind a proxy, so the Host header is whatever that proxy forwards.
    # Wrong here just means the setup panel shows a URL they have to correct,
    # never a broken request.
    PUBLIC_BASE_URL: str = LOCAL_BASE_URL

    # How long an identical TradingView body is treated as a replay rather
    # than a second decision. Only applies to alerts with no `id`; one with an
    # id is exactly idempotent and needs no window. Ten minutes is longer than
    # any duplicate a chart would legitimately send and short enough that a
    # genuinely repeated setup later in the session still gets through.
    TV_WEBHOOK_REPLAY_WINDOW_SEC: int = 600

    # How much the app says about itself. INFO is the useful default -- it is
    # what the worker uses to record what it actually did, and losing that was
    # the reason a strategy that should have signalled left no trace. Turn it
    # to WARNING only if the volume becomes a problem, which on one owner's
    # traffic it will not.
    LOG_LEVEL: str = "INFO"

    # A manual-confirm pending order older than this is stale and gets
    # auto-expired by the worker loop rather than sitting there forever.
    PENDING_ORDER_EXPIRY_MINUTES: int = 180

    # Whether order.created/order.updated/strategy.error events trigger real
    # LINE/Telegram/Email sends. Forced off in tests (see tests/conftest.py)
    # since the dispatcher opens its own DB session outside request scope,
    # bypassing the test DB override -- and to never hit real endpoints
    # during a test run regardless.
    NOTIFICATIONS_ENABLED: bool = True

    @property
    def public_base_url(self) -> str:
        """This deployment's own address, derived when nobody supplied one.

        Most container platforms hand the service its own address in the
        environment, so requiring somebody to copy that URL back into the
        service it came from is a step that exists for no reason -- and a step
        a first-time deployer skips, which produces a TradingView webhook
        pointed at localhost and no sign anywhere of why nothing arrives.

        WHICH platform is deliberately not this module's business:
        services.hosting knows the names, and knowing more than one is the
        difference between 「this works」 and 「this works if you chose the
        same host I did」.

        A FALLBACK, never an override: a custom domain is exactly the case no
        platform variable knows about, so an explicit value wins.
        """
        explicit = (self.PUBLIC_BASE_URL or "").strip()
        if explicit and explicit != LOCAL_BASE_URL:
            return explicit
        from app.services.hosting import public_url

        return public_url() or explicit

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Every value that ships in this repo (config defaults, .env.example). Anything
# still set to one of these is known to everyone who can read the repo.
_PLACEHOLDER_SECRETS = frozenset(
    {
        "dev-only-insecure-secret-change-me",
        "change-me",
        "change-me-to-a-long-random-string",
    }
)

# The VALUES are what goes wrong when the setting is missing, not secrets.
# bandit reads any string literal beside a key it considers secret-ish as a
# hardcoded password (B105); here the key is the setting's NAME and the value
# is the sentence printed at boot when it is unset.
# 一個隨機值要多長，才值得拿去推導一把金鑰。
#
# 推導不會憑空生出亂度：猜得到的字串推出來的金鑰也是猜得到的。部署平台的「自動產生」
# 給的值遠比這個長，所以這個門檻擋的是手打的東西，不是平台給的東西。
_MIN_DERIVABLE = 24

# 固定的鹽。它在這裡的作用是**領域分隔**（同一個環境變數不會推出跟別處一樣的金鑰），
# 不是每個使用者不同——它必須是固定的，因為同一個值每次都要推出同一把金鑰。不穩定的
# 話症狀是最壞的一種：存得進去、重啟之後解不開，中間沒有任何錯誤。
_KDF_SALT = b"stock-alerts/secret-encryption-key/v1"


@lru_cache(maxsize=8)
def fernet_key(raw: str) -> bytes | None:
    """把 SECRET_ENCRYPTION_KEY 變成一把 Fernet 用得了的金鑰，或 None（值不能用）。

    ＊ 為什麼要有這個。

    Fernet 只吃一種形狀（base64 的 32 bytes），而部署平台的「自動產生一個隨機值」給
    的是普通隨機字串。形狀不合，所以這一格以前只能由使用者自己在設定頁按「產生」、複
    製、回到平台後台貼上、存檔、等重新部署——七個動作，跨兩個網站，任何一步中斷就前功
    盡棄。而真的把這份東西交給目標使用者測試之後，回來的是「我不會用」。

    那個限制不是安全需求，是**格式需求**，而格式需求可以在這一邊解決。金鑰仍然只活在
    環境變數裡（資料庫整份被倒出去也拿不到它），使用者一次都不用碰。

    ＊ 已經是一把 Fernet 金鑰的話，原封不動地回傳。

    這一條不可以退。已經在跑的實例環境變數裡就是那種值，而資料庫裡有用它加密過的東
    西。這裡如果「順手也推導一次」，他所有的通知設定和券商金鑰會在下一次更新之後全部
    解不開——沒有錯誤訊息、沒有退路，而那是「更新不可以停掉已經在跑的那一份」最嚴重的
    一種違反。

    ＊ 用 scrypt 而不是雜湊一次。

    平台給的值亂度夠，雜湊一次就夠了；但這一格也可能是人手打的，而 scrypt 讓那種值
    不至於一推就穿。成本用 lru_cache 攤掉——一個行程裡只會算一次。
    """
    value = (raw or "").strip()
    if not value:
        return None

    try:
        Fernet(value.encode())
    except (ValueError, TypeError):
        pass
    else:
        return value.encode()

    if len(value) < _MIN_DERIVABLE:
        return None

    derived = Scrypt(salt=_KDF_SALT, length=32, n=2**14, r=8, p=1).derive(value.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


_REQUIRED_SECRETS = {
    "JWT_SECRET": "anyone could mint a login token for your account",  # nosec B105
    "TV_WEBHOOK_SECRET": "anyone could post fake TradingView signals",  # nosec B105
}


def verify_required_secrets(s: Settings) -> None:
    """Raise RuntimeError if a signing secret is unset or still a placeholder."""
    for name, consequence in _REQUIRED_SECRETS.items():
        value = getattr(s, name).strip()
        if not value or value in _PLACEHOLDER_SECRETS:
            raise RuntimeError(
                f"{name} is unset or still a placeholder -- refusing to start, because "
                f"{consequence}. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

    _verify_encryption_key(s)
    _verify_vapid(s)


def _verify_encryption_key(s: Settings) -> None:
    """Checked at boot rather than on first use.

    EncryptedJSON reads this key when a secret is actually written, so a
    deploy that forgot it came up green, passed its health check, and then
    threw an unexplained 500 the first time the owner tried to save a Telegram
    token or a broker key -- days later, on the one screen that matters. A
    malformed key does the same: present enough for a truthiness check, wrong
    enough to fail at the moment of use.
    """

    key = (s.SECRET_ENCRYPTION_KEY or "").strip()
    # NOT 「run this Python one-liner」 any more, on two counts. It is wrong --
    # any random string of 24+ characters works now, because fernet_key derives
    # a key from whatever it is given -- and it was always the wrong thing to
    # say to somebody who wants stock alerts on their phone and does not have
    # Python. That instruction is where the setup used to end.
    hint = (
        "It may be ANY random string of 24 or more characters. On a hosting "
        "platform, use its 「generate a value」 button for this variable "
        "(render.yaml already asks for that); anywhere else, any password "
        "manager's random-password generator will do."
    )
    if not key:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY is unset -- refusing to start, because broker "
            f"credentials and notification tokens cannot be stored without it. {hint}"
        )
    if fernet_key(key) is None:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY is too short to be usable -- refusing to start, "
            "because it would fail the first time a secret is saved rather than now. "
            f"It may be any random string of at least {_MIN_DERIVABLE} characters; "
            f"a hosting platform's 「generate a value」 button produces one. {hint}"
        )


def enforce_required_secrets(s: Settings) -> None:
    """Startup guard (called from app/main.py). Unlike SECRET_ENCRYPTION_KEY,
    which only fails when a stored secret is touched, a weak JWT_SECRET is
    silently exploitable, so the process must not come up at all.

    Escape hatch, in two parts, so nobody has to hand-generate a secret to work
    on this app: under pytest the guard is off, which keeps a fresh checkout's
    suite runnable with no configuration whatsoever; for a local `uvicorn` run,
    ALLOW_INSECURE_SECRETS=true in backend/.env opts out explicitly. Neither
    triggers by accident in a deployment -- nothing the app imports pulls in
    pytest, so `sys.modules` only holds it when a test runner started the
    process, and the flag defaults to false and is set nowhere in render.yaml.
    A deploy that forgets JWT_SECRET therefore fails loudly at boot instead of
    quietly serving forgeable tokens."""
    if s.ALLOW_INSECURE_SECRETS or "pytest" in sys.modules:
        return
    verify_required_secrets(s)


# Not a real address, and render.yaml deploys it verbatim. Kept as a named
# constant so the check and the file that ships it cannot drift apart.
_VAPID_SUBJECT_PLACEHOLDERS = frozenset(
    {"mailto:you@example.com", "mailto:admin@example.com", "mailto:me@example.com"}
)

_VAPID_HINT = "Regenerate a matching pair with: python scripts/generate_vapid_keys.py"


def _b64url(value: str) -> bytes:
    """Decode with or without padding -- some tools emit '=' and some strip
    it, and rejecting a perfectly good key over padding would be an outage we
    inflicted on ourselves."""
    text = value.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# 推導推播金鑰用的鹽。跟加密金鑰那一把分開，是**領域分隔**：同一個環境變數推出來的兩
# 把金鑰不可以互相推得出來。
_VAPID_SALT = b"stock-alerts/vapid/v1"


@lru_cache(maxsize=8)
def _derive_vapid(seed: str) -> tuple[str, str] | None:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    material = Scrypt(salt=_VAPID_SALT, length=32, n=2**14, r=8, p=1).derive(seed.encode("utf-8"))
    try:
        private = ec.derive_private_key(int.from_bytes(material, "big"), ec.SECP256R1())
    except ValueError:
        # 推出來的純量落在曲線的階之外。機率小到這輩子看不到一次，但「小到看不到」不
        # 是「不會發生」，而它發生時的症狀是開機直接炸掉。
        return None
    point = private.public_key().public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
    )
    scalar = material
    return (
        base64.urlsafe_b64encode(point).rstrip(b"=").decode(),
        base64.urlsafe_b64encode(scalar).rstrip(b"=").decode(),
    )


def vapid_keys(s: Settings) -> tuple[str | None, str | None]:
    """這一份部署要用哪一對推播金鑰。

    ＊ 為什麼要推導。

    這個產品的一句話是「想在手機上收到股票提醒」，而 iOS 的 Web Push 只在加到主畫面
    的網站上能用。所以推播不是加分項。但它要一對 P-256 金鑰，而部署平台生不出那種形
    狀，於是那三格只能是空白——「兩個都留空是合法設定」技術上對，實際上的意思是**照最
    短路徑走完的人永遠收不到手機通知，而且沒有任何東西會說**。

    私鑰就是 32 bytes 的純量，而這一份部署已經有一個高亂度、只活在環境變數裡、**本來
    就必須固定不變**的秘密（換掉 SECRET_ENCRYPTION_KEY 等於丟掉所有加密過的資料）。
    用不同的鹽推導，就有了一對穩定的金鑰——不用開新資料表、不用遷移、不用多存一個秘
    密，也沒有多一個會被倒出去的地方。

    ＊ 只在兩個都沒設的時候才推導。

    這一條讓這個改動對已經在跑的部署**零風險**：有設的照用自己那一對；沒設的本來就沒
    有推播，也就沒有任何訂閱會被弄壞。反過來做的話，那些人手機上依舊公鑰建立的訂閱會
    全部失效，而症狀是靜默的——推播照送、對方回 403、沒有人看得到。
    """
    public = (s.VAPID_PUBLIC_KEY or "").strip()
    private = (s.VAPID_PRIVATE_KEY or "").strip()
    if public or private:
        # 半對是設定錯誤，由 _verify_vapid 擋開機。這裡原樣回傳，讓那個檢查去說話。
        return (public or None, private or None)

    seed = (s.SECRET_ENCRYPTION_KEY or "").strip()
    if fernet_key(seed) is None:
        return (None, None)
    derived = _derive_vapid(seed)
    return derived or (None, None)


def vapid_subject(s: Settings) -> str:
    """推播服務要找誰。RFC 8292 允許 mailto: 或 https:。

    預設值 `mailto:admin@example.com` 是**別人的信箱**。而這個 app 知道自己的網址，那
    是一個真的、不需要跟使用者要的答案——順帶也不用把他的信箱送給 Apple 和 Google。
    """
    subject = (s.VAPID_SUBJECT or "").strip()
    if subject and subject not in _VAPID_SUBJECT_PLACEHOLDERS:
        return subject
    base = s.public_base_url.rstrip("/")
    return base if base.startswith("https://") else subject


def _verify_vapid(s: Settings) -> None:
    """Refuse to start on a VAPID configuration that cannot deliver.

    VAPID_PUBLIC_KEY is handed to the browser and baked into every
    subscription it creates; VAPID_PRIVATE_KEY signs every push. Nothing
    checked they were the same pair, so regenerating one and not the other --
    or pasting the wrong half into Render -- made Apple answer 403
    VapidPkHashMismatch to every push, forever. The app booted green, the
    health check passed, a channel could be created and reported success, and
    not one alert was ever delivered.

    That silence is the failure this product cannot have, so this follows
    _verify_encryption_key's precedent and stops the boot. A deploy that fails
    immediately and says why is far cheaper than one that delivers nothing.

    Both keys empty is a valid configuration and boots: web push is one channel
    of four, and somebody using only Telegram and email must not be blocked.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public = (s.VAPID_PUBLIC_KEY or "").strip()
    private = (s.VAPID_PRIVATE_KEY or "").strip()

    if not public and not private:
        # 兩邊都沒設現在代表**由 SECRET_ENCRYPTION_KEY 推導**（見 vapid_keys），不是
        # 關掉。推出來的一定成對，所以這裡沒有東西要驗；推不出來（沒有加密金鑰）就是
        # 真的沒有推播，而那由設定頁去說。
        return

    if not public or not private:
        missing = "VAPID_PUBLIC_KEY" if not public else "VAPID_PRIVATE_KEY"
        raise RuntimeError(
            f"{missing} is empty while the other half is set -- refusing to start. "
            "Half a pair can only be a mistake, and it produces the same silent "
            f"403 on every push as a mismatched one. {_VAPID_HINT}"
        )

    try:
        scalar = int.from_bytes(_b64url(private), "big")
        derived = (
            ec.derive_private_key(scalar, ec.SECP256R1())
            .public_key()
            .public_bytes(encoding=Encoding.X962, format=PublicFormat.UncompressedPoint)
        )
    except Exception as exc:
        raise RuntimeError(
            "VAPID_PRIVATE_KEY is not a valid P-256 private key -- refusing to start, "
            "because it would fail on every push instead of now. Expected the raw "
            f"32-byte scalar, base64url-encoded. {_VAPID_HINT}"
        ) from exc

    try:
        configured = _b64url(public)
    except Exception as exc:
        raise RuntimeError(
            "VAPID_PUBLIC_KEY is not valid base64url -- refusing to start. Expected an "
            f"X9.62 uncompressed point (65 bytes starting with 0x04). {_VAPID_HINT}"
        ) from exc

    if configured != derived:
        raise RuntimeError(
            "VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are not the same key pair -- "
            "refusing to start. The browser bakes the public key into every "
            "subscription and the private key signs every push, so a mismatch makes "
            "the push service reject all of them (Apple: 403 VapidPkHashMismatch) "
            "while everything else looks healthy. "
            f"{_VAPID_HINT} -- and note that changing the pair invalidates every "
            "existing subscription, so each device has to set up push again."
        )

    subject = (s.VAPID_SUBJECT or "").strip()

    # NOT a URI at all: RFC 8292 requires mailto: or https:, so this is
    # definitely wrong and definitely fixable. A bare email address looks right
    # and is not, which is exactly what gets pasted in.
    if not subject.startswith(("mailto:", "https://")):
        raise RuntimeError(
            f"VAPID_SUBJECT must be a mailto: or https: URI (RFC 8292), got {subject!r} "
            "-- refusing to start. A bare email address looks right and is not."
        )

    # A syntactically valid placeholder is only PROBABLY wrong, and the
    # difference matters. Whether a push service actually rejects a well-formed
    # but fictitious mailto is not documented by Apple either way, so refusing
    # to boot over it would risk taking the whole alerting system down for
    # something that may be working fine -- the exact opposite of what this
    # function is for. It gets said loudly instead, on every start.
    if subject in _VAPID_SUBJECT_PLACEHOLDERS:
        logging.getLogger("app.config").warning(
            "VAPID_SUBJECT is still the placeholder %r. It is nobody's address, and a "
            "push service is entitled to reject a contact it cannot use -- set it to "
            "your own mailto: address. Not fatal: whether it is actually refused is "
            "undocumented, and stopping the app over a maybe would be worse.",
            subject,
        )
