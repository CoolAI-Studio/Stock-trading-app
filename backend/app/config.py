from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite:///./trading_app_dev.db"

    JWT_SECRET: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    SECRET_ENCRYPTION_KEY: str = ""

    TV_WEBHOOK_SECRET: str = "change-me"

    CORS_ORIGINS: str = "http://localhost:5173"

    AI_PROVIDER: str = "openai_compatible"
    AI_BASE_URL: str = "https://openrouter.ai/api/v1"
    AI_API_KEY: str = ""
    AI_MODEL: str = ""

    MARKET_DATA_POLL_INTERVAL_SEC: float = 5.0

    WS_TICKET_TTL_SECONDS: int = 30

    # Closed by default: with registration off, a publicly reachable backend only
    # exposes login + the secret-gated TradingView webhook. First user is created
    # via scripts/create_user.py.
    ALLOW_REGISTRATION: bool = False

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
