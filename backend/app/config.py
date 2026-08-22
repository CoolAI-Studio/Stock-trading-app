import base64
import logging
import sys
from functools import lru_cache

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

    # /healthz fails (503) when the worker's last loop iteration, or its last
    # poll cycle that completed, is older than this. Deliberately far above
    # MARKET_DATA_POLL_INTERVAL_SEC: one slow yfinance response must never page
    # anyone, only a worker that is genuinely wedged or dead.
    HEALTH_MAX_AGE_SEC: float = 300.0

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
    from cryptography.fernet import Fernet

    key = (s.SECRET_ENCRYPTION_KEY or "").strip()
    hint = (
        "Generate one with: "
        'python -c "from cryptography.fernet import Fernet; '
        'print(Fernet.generate_key().decode())"'
    )
    if not key:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY is unset -- refusing to start, because broker "
            f"credentials and notification tokens cannot be stored without it. {hint}"
        )
    try:
        Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY is not a valid Fernet key -- refusing to start, "
            "because it would fail the first time a secret is saved rather than now. "
            f"{hint}"
        ) from exc


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
        return  # web push switched off; nothing to check, including the subject

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
