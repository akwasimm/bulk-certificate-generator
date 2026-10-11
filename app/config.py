"""Application settings, loaded from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Bulk Certificate Generator"
    debug: bool = False
    enable_docs: bool = True

    database_url: str = "sqlite:///./certificates.db"
    storage_dir: str = "./storage/certificates"

    max_recipients_per_job: int = 1000
    max_workers: int = 4

    # Empty string disables API-key auth (convenient for local runs and tests).
    api_key: str = ""

    # Comma-separated list of allowed origins, or "*" for any.
    cors_origins: str = "*"

    # Background jobs. With a broker reachable, jobs run on Celery workers; in
    # eager mode they run inline in the request (used by the test suite, so no
    # broker is required to test the full flow).
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str | None = None
    celery_task_always_eager: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
