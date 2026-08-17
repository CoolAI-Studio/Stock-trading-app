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

    # Must run with --workers 1 when true: two worker processes would each run
    # their own polling loop and duplicate signals for the same tick. Tests
    # force this off (see tests/conftest.py) so the suite never spins up a
    # real polling loop or touches the network.
    WORKER_ENABLED: bool = True

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
