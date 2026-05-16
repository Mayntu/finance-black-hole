from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str
    OPENAI_API_KEY: str

    DATABASE_URL: str = "postgresql+asyncpg://fbh:fbhpass@postgres:5432/financeblackhole"
    REDIS_URL: str = "redis://redis:6379/0"

    WEBHOOK_URL: str = ""
    WEBHOOK_SECRET: str = "webhook_secret"

    # Public HTTPS URL for Telegram Mini App (defaults to WEBHOOK_URL if empty)
    WEBAPP_URL: str = ""

    JWT_SECRET: str = "jwt_secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 24


settings = Settings()
