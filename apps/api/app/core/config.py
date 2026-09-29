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

    # Web app origin (e.g. http://localhost:3000): where Keycloak's invitation emails send users
    # after they set a password.
    web_origin: str | None = None

    # ---- S3 (MinIO locally): CSV imports and, later, learning content.
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_bucket: str = "skillifyme-local"
    # Browser-reachable S3 endpoint for presigned URLs (the in-network one is S3_ENDPOINT_URL).
    s3_public_endpoint_url: str | None = None

    # ---- Video: `local` (MinIO; dev and CI) or `bunny` (Bunny Stream).
    video_provider: Literal["local", "bunny"] = "local"
    video_upload_max_bytes: int = Field(default=2 * 1024**3, ge=1)
    video_upload_ttl_seconds: int = Field(default=3600, ge=60)
    video_playback_ttl_seconds: int = Field(default=300, ge=60, le=86400)
    bunny_library_id: str | None = None
    bunny_api_key: SecretStr | None = None
    bunny_cdn_hostname: str | None = None  # e.g. vz-xxxxxxxx-xxx.b-cdn.net
    bunny_token_key: SecretStr | None = None  # CDN token authentication key
    bunny_webhook_secret: SecretStr | None = None
    bunny_api_url: str = "https://video.bunnycdn.com"

    # ---- Public catalog: after a publish or archive commits, a Celery task asks the web app to
    # revalidate its statically generated catalog pages (POST <web>/api/revalidate).
    web_internal_url: str | None = None  # e.g. http://web:3000 (unset: revalidation is skipped)
    revalidate_secret: SecretStr | None = None  # shared with the web app's REVALIDATE_SECRET

    # ---- Files: PDFs and notes images (presigned POST uploads, signed GET downloads).
    pdf_upload_max_bytes: int = Field(default=25 * 1024**2, ge=1)
    image_upload_max_bytes: int = Field(default=5 * 1024**2, ge=1)
    file_upload_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    file_download_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # ---- Video watch progress (heartbeats buffered in Redis, flushed by Celery beat).
    heartbeat_interval_seconds: int = Field(default=15, ge=5)
    progress_flush_interval_seconds: float = Field(default=30.0, gt=0)

    # ---- Rate limits (Redis sliding windows).
    rl_auth_failures_per_minute: int = Field(default=30, ge=1)  # per client IP
    rl_invites_per_hour: int = Field(default=60, ge=1)  # per acting user
    rl_imports_per_hour: int = Field(default=10, ge=1)  # per acting user

    # ---- CSV student import limits.
    import_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    import_max_rows: int = Field(default=10_000, ge=1)
    import_chunk_size: int = Field(default=500, ge=1, le=1000)
    invitation_ttl_days: int = Field(default=14, ge=1)

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
    def s3_browser_endpoint_url(self) -> str | None:
        return self.s3_public_endpoint_url or self.s3_endpoint_url

    @property
    def bunny_configured(self) -> bool:
        return all(
            (self.bunny_library_id, self.bunny_api_key, self.bunny_cdn_hostname,
             self.bunny_token_key, self.bunny_webhook_secret)
        )  # fmt: skip

    @property
    def celery_broker(self) -> str:
        url = self.celery_broker_url or self.redis_url
        return url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment
