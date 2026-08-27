import ipaddress
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_LOCAL_URI = "postgresql+asyncpg://postgres:postgres@localhost:5432/opencitadel"
_PLACEHOLDER_MARKERS = (
    "change-in-production",
    "change-me",
    "replace-with",
    "changeme",
)


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


class DeploymentSettings(BaseSettings):
    """Restart-bound process topology, connectivity, and secrets."""

    # 项目基础
    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    cors_origins: str = "*"
    otel_enabled: bool = False
    otel_service_name: str = "opencitadel-api"
    otel_exporter_endpoint: str = ""
    api_key_secret: str = "opencitadel-api-key-secret-change-in-production"
    api_key_secret_id: str = "primary"
    api_key_previous_secrets: dict[str, str] = Field(default_factory=dict)
    audit_signing_key: str = "opencitadel-audit-signing-key-change-in-production"
    audit_signing_key_id: str = "primary"
    audit_previous_signing_keys: dict[str, str] = Field(default_factory=dict)
    jwt_secret: str = "opencitadel-jwt-secret-change-in-production"
    session_secret: str = "opencitadel-session-secret-change-in-production"
    sandbox_broker_url: str = ""
    sandbox_broker_token: str = ""
    sandbox_driver: str = "auto"
    sandbox_address: str = ""
    sandbox_image: str = ""
    sandbox_name_prefix: str = ""
    sandbox_network: str = ""
    sandbox_chrome_args: str = ""
    sandbox_https_proxy: str = ""
    sandbox_http_proxy: str = ""
    sandbox_no_proxy: str = ""
    sandbox_k8s_namespace: str = "default"
    sandbox_k8s_pod_label: str = "app=opencitadel-sandbox"
    policy_head_refresh_interval_seconds: float = 5.0
    policy_max_staleness_seconds: float = 30.0
    shutdown_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS",
            "shutdown_timeout_seconds",
        ),
    )
    # 8090/8091: bundled Ops Patrol Collector/Actuator (docker-compose.yml), the
    # only registered-MCP-server ports outside the plain HTTP/HTTPS defaults.
    outbound_allowed_ports: str = "80,443,8080,8443,8090,8091,11434"
    outbound_private_host_allowlist: str = ""
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    cookie_domain: str = ""
    cookie_secure: bool = False
    oauth_redirect_base: str = "http://localhost:8088/api/auth/oauth"
    frontend_base_url: str = "http://localhost:3000"
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    # Administrator seeding is opt-in. Deployment manifests and .env.example
    # configure the initial account explicitly; library/test startup remains
    # valid without silently assuming an identity whose password is unknown.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    # Fixture replay is a process-local test/demo mechanism, never a runtime
    # product capability and never allowed when ENV=production.
    patrol_fixture_replay_enabled: bool = False

    # 数据库连接（引导层，启动前必须可用）
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "opencitadel"
    postgres_host: str = "localhost"
    sqlalchemy_database_uri: str = ""
    postgres_admin_user: str = "postgres"
    postgres_admin_password: str = ""
    sqlalchemy_migration_database_uri: str = ""
    sqlalchemy_echo: bool = False
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 5
    postgres_pool_recycle_seconds: int = 1800

    # Redis 连接
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    # 对象存储：cos（默认）或 minio
    storage_provider: str = "cos"

    # 腾讯云 COS 密钥与桶
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_scheme: str = "https"
    cos_bucket: str = ""
    cos_domain: str = ""

    # MinIO（STORAGE_PROVIDER=minio 时生效）
    minio_endpoint: str = "opencitadel-minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "opencitadel"
    minio_secure: bool = False
    minio_public_endpoint: str = ""

    # Prometheus 指标：metrics_token 为空表示 /api/metrics 关闭（404，fail-closed）；
    # execution_kernel_metrics_port 为 0 表示执行内核指标端口关闭。
    metrics_token: str = ""
    execution_kernel_metrics_port: int = 9108

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def derive_sqlalchemy_database_uri(self) -> "DeploymentSettings":
        uri = (self.sqlalchemy_database_uri or "").strip()
        if not uri or uri == _DEFAULT_LOCAL_URI:
            user = quote_plus(self.postgres_user)
            password = quote_plus(self.postgres_password)
            object.__setattr__(
                self,
                "sqlalchemy_database_uri",
                f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:5432/{self.postgres_db}",
            )
        if self.env.lower() == "production":
            insecure_values = {
                "api_key_secret": "opencitadel-api-key-secret-change-in-production",
                "jwt_secret": "opencitadel-jwt-secret-change-in-production",
                "session_secret": "opencitadel-session-secret-change-in-production",
            }
            for field, default in insecure_values.items():
                if getattr(self, field) == default:
                    raise ValueError(f"{field} must be changed in production")
            secret_fields = (
                "api_key_secret",
                "audit_signing_key",
                "jwt_secret",
                "session_secret",
            )
            for field in secret_fields:
                value = getattr(self, field)
                if len(value) < 32:
                    raise ValueError(f"{field} must contain at least 32 characters in production")
                if _looks_like_placeholder(value):
                    raise ValueError(f"{field} must not contain a placeholder value in production")
            secret_values = [getattr(self, field) for field in secret_fields]
            if len(set(secret_values)) != len(secret_values):
                raise ValueError(
                    "audit_signing_key, api_key_secret, jwt_secret, and "
                    "session_secret must be distinct in production"
                )
            if not self.api_key_secret_id.strip():
                raise ValueError("api_key_secret_id must be set in production")
            if not self.audit_signing_key_id.strip():
                raise ValueError("audit_signing_key_id must be set in production")
            if self.sandbox_broker_url and len(self.sandbox_broker_token) < 32:
                raise ValueError(
                    "sandbox_broker_token must contain at least 32 characters "
                    "when sandbox_broker_url is configured"
                )
            if self.sandbox_broker_url and _looks_like_placeholder(self.sandbox_broker_token):
                raise ValueError(
                    "sandbox_broker_token must not contain a placeholder value in production"
                )
            if not self.cookie_secure:
                raise ValueError("cookie_secure must be true in production")
            if len(self.bootstrap_admin_password) < 12 or _looks_like_placeholder(
                self.bootstrap_admin_password
            ):
                raise ValueError(
                    "bootstrap_admin_password must contain at least 12 characters in production"
                )
            if (
                self.postgres_password == "postgres"
                or len(self.postgres_password) < 16
                or _looks_like_placeholder(self.postgres_password)
            ):
                raise ValueError(
                    "postgres_password must be changed and contain at least "
                    "16 characters in production"
                )
            if (
                not self.redis_password
                or len(self.redis_password) < 16
                or _looks_like_placeholder(self.redis_password)
            ):
                raise ValueError("redis_password must contain at least 16 characters in production")
        try:
            for value in self.trusted_proxy_cidrs.split(","):
                if value.strip():
                    ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            raise ValueError("trusted_proxy_cidrs contains an invalid CIDR") from exc
        try:
            ports = {
                int(value.strip())
                for value in self.outbound_allowed_ports.split(",")
                if value.strip()
            }
        except ValueError as exc:
            raise ValueError("outbound_allowed_ports must contain integers") from exc
        if not ports or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("outbound_allowed_ports contains an invalid port")
        if self.policy_head_refresh_interval_seconds <= 0:
            raise ValueError("policy_head_refresh_interval_seconds must be positive")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        if self.policy_max_staleness_seconds <= self.policy_head_refresh_interval_seconds:
            raise ValueError(
                "policy_max_staleness_seconds must exceed policy_head_refresh_interval_seconds"
            )
        return self


def sqlalchemy_sync_database_uri(settings: DeploymentSettings) -> str:
    """Return sync SQLAlchemy URL (psycopg2) from bootstrap settings."""
    return settings.sqlalchemy_database_uri.replace("+asyncpg", "+psycopg2")


def sqlalchemy_sync_migration_database_uri(
    settings: DeploymentSettings,
) -> str:
    """Return the privileged DDL URL used only by Alembic."""
    explicit = settings.sqlalchemy_migration_database_uri.strip()
    if explicit:
        return explicit.replace("+asyncpg", "+psycopg2")
    admin_user = settings.postgres_admin_user.strip()
    admin_password = settings.postgres_admin_password
    if admin_user and admin_password:
        user = quote_plus(admin_user)
        password = quote_plus(admin_password)
        return (
            f"postgresql+psycopg2://{user}:{password}@"
            f"{settings.postgres_host}:5432/{settings.postgres_db}"
        )
    if settings.env.lower() == "production":
        raise ValueError("migration database credentials are required in production")
    return sqlalchemy_sync_database_uri(settings)


def load_deployment_settings() -> DeploymentSettings:
    """Load and validate one independent restart-bound settings value."""
    return DeploymentSettings()
