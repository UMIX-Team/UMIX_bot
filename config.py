"""Конфигурация UpdoUP."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    BOT_TOKEN: str
    OWNER_ID: int

    # Test defaults
    TARGET_URL: str = "https://rlfleague.ru"
    MAX_VU: int = 50                  # осторожно для 1 CPU
    ERROR_RATE_LIMIT: float = 0.02    # 2%
    LATENCY_P95_LIMIT: int = 1500     # ms
    LATENCY_P99_LIMIT: int = 3000     # ms


settings = Settings()
