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

    # Passwords for the non-owner login roles, used only by `python -m app.cli.db_roles`.
    app_db_password: SecretStr | None = None
    relay_db_password: SecretStr | None = None

    redis_url: SecretStr

    # Kafka / outbox relay. The relay connects as the `skillify_relay` role.
    kafka_bootstrap_servers: str = "localhost:19092"
    relay_database_url: SecretStr | None = None
    outbox_relay_batch_size: int = Field(default=500, ge=1, le=5000)
    outbox_relay_poll_interval_seconds: float = Field(default=0.5, gt=0)

    # Celery defaults to the main Redis instance when unset.
    celery_broker_url: SecretStr | None = None

    health_check_timeout_seconds: float = Field(default=2.0, gt=0)

    # ---- Keycloak / OIDC: every URL derives from KEYCLOAK_PORT and KEYCLOAK_REALM unless set.
    # Required: comes from KEYCLOAK_PORT (see .env.example).
    keycloak_port: int = Field(ge=1, le=65535)
    keycloak_realm: str = "skillifyme"
    # Browser-facing base URL. Keycloak pins its issuer to this (KC_HOSTNAME), so every token
    # carries iss=<public url>/realms/<realm>, no matter which host requested it.
    keycloak_public_url: str | None = None
    # Where this process reaches Keycloak (JWKS, admin API). In docker: http://keycloak:<port>.
    keycloak_internal_url: str | None = None
    kc_admin_client_id: str = "skillifyme-admin"
    kc_admin_client_secret: SecretStr | None = None
    # Dev realm only: the password-grant client used by automated tests (never set in production).
    kc_test_client_secret: SecretStr | None = None
    oidc_audience: str = "skillifyme-api"
    # Clients (`azp`) whose access tokens are accepted.
    oidc_allowed_clients: list[str] = Field(default_factory=lambda: ["skillifyme-web"])
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    jwks_cache_ttl_seconds: int = Field(default=600, ge=10)
    # Minimum gap between refetches triggered by an unknown `kid` (key rotation / junk tokens).
    jwks_refetch_cooldown_seconds: float = Field(default=30.0, ge=0)
    principal_cache_ttl_seconds: int = Field(default=60, ge=0)

    # OpenTelemetry is disabled unless an OTLP endpoint is configured.
    otel_exporter_otlp_endpoint: str | None = None

    @property
    def keycloak_base_url(self) -> str:
        return (self.keycloak_public_url or f"http://localhost:{self.keycloak_port}").rstrip("/")

    @property
    def keycloak_backchannel_url(self) -> str:
        return (self.keycloak_internal_url or self.keycloak_base_url).rstrip("/")

    @property
    def oidc_issuer(self) -> str:
        return f"{self.keycloak_base_url}/realms/{self.keycloak_realm}"

    @property
    def oidc_jwks_url(self) -> str:
        return (
            f"{self.keycloak_backchannel_url}/realms/{self.keycloak_realm}"
            "/protocol/openid-connect/certs"
        )

    @property
    def oidc_token_url(self) -> str:
        """Token endpoint over the backchannel (service-account logins)."""
        return (
            f"{self.keycloak_backchannel_url}/realms/{self.keycloak_realm}"
            "/protocol/openid-connect/token"
        )

    @property
    def keycloak_admin_api_url(self) -> str:
        return f"{self.keycloak_backchannel_url}/admin/realms/{self.keycloak_realm}"

    @property
    def celery_broker(self) -> str:
        url = self.celery_broker_url or self.redis_url
        return url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment
