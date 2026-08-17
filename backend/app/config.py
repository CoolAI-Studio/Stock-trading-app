import sys
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    ALLOW_REGISTRATION: bool = False

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

_REQUIRED_SECRETS = {
    "JWT_SECRET": "anyone could mint a login token for your account",
    "TV_WEBHOOK_SECRET": "anyone could post fake TradingView signals",
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
