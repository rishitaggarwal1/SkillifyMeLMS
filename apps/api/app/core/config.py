"""Application settings, loaded from environment variables (see the repo-root .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win. The repo-root .env is used when running on the host from apps/api;
        # inside containers everything comes from real environment variables.
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    service_name: str = "skillifyme-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # JSON logs everywhere by default; set LOG_JSON=false for human-readable local output.
    log_json: bool = True

    # Runtime connection as the non-owner application role, so RLS applies.
    database_url: SecretStr
    # Owner connection, used only by Alembic migrations.
    migration_database_url: SecretStr
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_statement_timeout_ms: int = Field(default=15_000, ge=0)

    redis_url: SecretStr

    # Celery defaults to the main Redis instance when unset.
    celery_broker_url: SecretStr | None = None

    health_check_timeout_seconds: float = Field(default=2.0, gt=0)

    # OpenTelemetry is disabled unless an OTLP endpoint is configured.
    otel_exporter_otlp_endpoint: str | None = None

    @property
    def celery_broker(self) -> str:
        url = self.celery_broker_url or self.redis_url
        return url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment
