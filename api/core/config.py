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


# RFC1918 private IPv4 ranges. The sandbox / Kubernetes pod network shares these
# ranges (docker-compose bridges land in 172.16/12, K8s pod CIDRs in 10/8), so a
# broad private CIDR in trusted_proxy_cidrs would let a compromised sandbox that
# dials the API directly forge X-Forwarded-For and poison the HMAC-signed audit
# actor_ip / bypass per-IP rate limiting. A real reverse proxy / ingress is a
# handful of exact hosts, so production must pin narrow CIDRs (>= /24, typically
# a /32). Loopback (127.0.0.0/8, ::1) and IPv6 are exempt from this check.
_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_MIN_TRUSTED_PROXY_PREFIXLEN = 24


def _is_overbroad_private_proxy_cidr(network: ipaddress._BaseNetwork) -> bool:
    """Return True for an RFC1918 IPv4 range wider than /24 (e.g. 10/8, /12, /16)."""
    if not isinstance(network, ipaddress.IPv4Network):
        return False
    if network.prefixlen >= _MIN_TRUSTED_PROXY_PREFIXLEN:
        return False
    return any(network.subnet_of(rfc1918) for rfc1918 in _RFC1918_NETWORKS)


class DeploymentSettings(BaseSettings):
    """Restart-bound process topology, connectivity, and secrets."""

    # 项目基础
    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    # 跨域来源列表（逗号分隔）。空串与 "*" 的实际效果相同：因为携带凭据
    # (allow_credentials=True) 时通配符不合法，两者都会被折叠为“不放行任何
    # 跨域来源”（仅 nginx 同源访问）。需要跨域时必须显式列出来源。
    cors_origins: str = ""
    otel_enabled: bool = False
    otel_service_name: str = "opencitadel-api"
    otel_exporter_endpoint: str = ""
    api_key_secret: str = "opencitadel-api-key-secret-change-in-production"
    api_key_secret_id: str = "primary"
    api_key_previous_secrets: dict[str, str] = Field(default_factory=dict)
    # 公共事件游标（SSE cursor）HMAC 密钥（K4-5，建议级）。留空时回退为
    # sha256(api_key_secret) 派生值（与历史部署行为一致）；显式配置后游标签名
    # 与 API key 加密的信任域分离。解码兼容双密钥（新配置值 + 派生回退值），
    # 用于运行期密钥轮换窗口内旧游标的平滑失效。
    public_cursor_secret: str = ""
    audit_signing_key: str = "opencitadel-audit-signing-key-change-in-production"
    audit_signing_key_id: str = "primary"
    audit_previous_signing_keys: dict[str, str] = Field(default_factory=dict)
    jwt_secret: str = "opencitadel-jwt-secret-change-in-production"
    jwt_previous_secrets: dict[str, str] = Field(default_factory=dict)
    session_secret: str = "opencitadel-session-secret-change-in-production"
    # Signing secret for the database authorization context HMAC (the
    # `app.auth_signature` GUC validated by the RLS policies). Defaults to
    # session_secret when unset so existing deployments keep the exact same
    # behaviour, but can be configured independently to separate the
    # Starlette session-cookie trust domain from the DB authorization one.
    database_authorization_signing_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DATABASE_AUTHORIZATION_SIGNING_SECRET",
            "database_authorization_signing_secret",
        ),
    )
    sandbox_broker_url: str = ""
    sandbox_broker_token: str = ""
    # Deployment-wide secret seed used to derive per-sandbox data-plane bearer
    # tokens (HMAC(seed, sandbox_id)). Shared by api + execution-kernel so any
    # replica re-attaching to a sandbox computes the same token; it is never
    # injected into the untrusted sandbox container (only the derived token is).
    # Required and >=32 chars in production.
    sandbox_token_seed: str = ""
    # Bearer token the Ops Collector MCP server requires on its streamable-http
    # endpoint; the demo seed stamps it into the registered MCPServerRecord's
    # Authorization header. Empty means no header is injected (stdio / legacy).
    # The Actuator's token is NOT mirrored here: its Authorization header is
    # configured on the MCP Server registration (Settings -> Integrations), and
    # OPS_ACTUATOR_TOKEN belongs to the actuator server process itself.
    ops_collector_token: str = ""
    sandbox_driver: str = "auto"
    sandbox_address: str = ""
    sandbox_image: str = ""
    sandbox_name_prefix: str = ""
    sandbox_network: str = ""
    sandbox_labels: dict[str, str] = Field(default_factory=dict)
    sandbox_chrome_args: str = ""
    sandbox_https_proxy: str = ""
    sandbox_http_proxy: str = ""
    sandbox_no_proxy: str = ""
    sandbox_k8s_namespace: str = "default"
    sandbox_k8s_pod_label: str = "app=opencitadel-sandbox"
    policy_head_refresh_interval_seconds: float = 5.0
    policy_max_staleness_seconds: float = 30.0
    # 三段式优雅关停的排水窗口（K2-3）：先停新 claim，再等在途 handler 自然完成
    # 至多这么多秒（覆盖典型模型调用时长），超时才 cancel。
    shutdown_timeout_seconds: float = Field(
        default=90.0,
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

    # 出站邮件（SMTP）：smtp_host 留空则邮件通知渠道不可用（优雅降级）
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

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

    # 执行内核批量/背压/轮询（P1-3）。
    # execution_activity_max_concurrency 是 ActivityWorker 每进程并发执行 claim
    # 的上限（Semaphore）。每个 claim 内含多次数据库连接 checkout，因此并发上限
    # 必须 <= 连接池有效容量（postgres_pool_size + postgres_max_overflow），否则
    # 高并发下会出现连接排队 / 死锁向量。保持默认 8 <= 10（5+5）。
    execution_activity_batch_size: int = 100
    execution_activity_max_concurrency: int = 8
    execution_idle_poll_seconds: float = 1.0
    # Activity 毒丸防护（K2-2）：单个 activity task 行被 claim 的总次数上限
    # （含租约过期后的重捞）。超限转 dead_lettered，不再被 claim；对应 Run 由
    # 活动超时 timer（FailActivity/ACTIVITY_TIMEOUT）兜底收敛。
    execution_activity_max_claim_attempts: int = 5
    # Inbox 毒丸防护（K2-5）：单条命令被 claim 的总次数上限，超限转 dead_lettered
    # 终态（不再投递），命令以 COMMAND_DEAD_LETTERED 拒绝码结算。
    execution_inbox_max_claim_attempts: int = 10
    # 执行队列生命周期（K2-5，D7）：调度器 leader tick 分批清理已完成的
    # inbox/outbox 行（默认 7 天）与 timer/activity 终态行（默认 30 天）。
    # activity 终态行仅在所属 Run 已终态后才可清理（活跃 Run 的决策水合依赖
    # decision_payload 列，这是硬约束）。0 = 关闭该队列的自动清理。
    execution_inbox_retention_days: int = 7
    # Dead-lettered inbox rows are operator diagnostics: kept longer than
    # settled rows, but not forever. 0 disables the purge.
    execution_inbox_dead_letter_retention_days: int = 30
    execution_outbox_retention_days: int = 7
    execution_timer_retention_days: int = 30
    execution_activity_retention_days: int = 30
    execution_queue_purge_batch_size: int = 500
    # 准入背压（K2-8）：单个 owner scope（用户或团队）的活跃（非终态）Run 上限，
    # 超限以 ADMISSION_LIMIT_EXCEEDED 拒绝新 Run。0 = 不限制。
    execution_max_active_runs_per_scope: int = 200

    # 回收站保留期：软删的会话/知识库超期后由调度器 leader tick 自动物理清除
    # （每 tick 限一批，清除动作写审计）。0 = 关闭自动清理，仅保留手动 purge。
    recycle_bin_retention_days: int = 30
    recycle_bin_purge_batch_size: int = 100

    # Web 搜索提供方：searxng | tavily | bing_api | bing_html（默认，HTML 抓取，
    # 脆弱且可能被反爬）| none（不注册搜索工具）。API 型提供方失败显式报错，
    # 不会静默返回空结果。
    search_provider: str = "bing_html"
    search_endpoint: str = ""
    search_api_key: str = ""

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
        # The DB authorization signing secret falls back to session_secret when
        # not explicitly configured, so existing deployments (and the RLS values
        # seeded from `app.rls_signing_secret` = session_secret) keep working
        # unchanged. Record whether it was set explicitly for production checks.
        explicit_db_auth_signing_secret = (self.database_authorization_signing_secret or "").strip()
        if not explicit_db_auth_signing_secret:
            object.__setattr__(
                self,
                "database_authorization_signing_secret",
                self.session_secret,
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
            # When the DB authorization signing secret is configured
            # independently (i.e. not falling back to session_secret) it must be
            # strong and, per the trust-domain split, distinct from the other
            # secrets. Left as a soft check: not enforcing distinctness when it
            # simply mirrors session_secret keeps the RLS default intact.
            if (
                explicit_db_auth_signing_secret
                and explicit_db_auth_signing_secret != self.session_secret
            ):
                if len(explicit_db_auth_signing_secret) < 32:
                    raise ValueError(
                        "database_authorization_signing_secret must contain at "
                        "least 32 characters in production"
                    )
                if _looks_like_placeholder(explicit_db_auth_signing_secret):
                    raise ValueError(
                        "database_authorization_signing_secret must not contain "
                        "a placeholder value in production"
                    )
                if explicit_db_auth_signing_secret in set(secret_values):
                    raise ValueError(
                        "database_authorization_signing_secret must be distinct "
                        "from the other secrets in production"
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
            if len(self.sandbox_token_seed) < 32 or _looks_like_placeholder(
                self.sandbox_token_seed
            ):
                raise ValueError(
                    "sandbox_token_seed must contain at least 32 characters in production"
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
            # MinIO's bundled default credentials (minioadmin) are only safe for
            # the local profile; a production deployment that actually serves
            # objects from MinIO must supply real credentials. COS-backed
            # production (the default storage_provider) is unaffected.
            if self.storage_provider.strip().lower() == "minio":
                for field in ("minio_access_key", "minio_secret_key"):
                    value = getattr(self, field)
                    if not value or value == "minioadmin" or _looks_like_placeholder(value):
                        raise ValueError(
                            f"{field} must not use the default 'minioadmin' "
                            "credential in production"
                        )
        try:
            trusted_proxy_networks = [
                ipaddress.ip_network(value.strip(), strict=False)
                for value in self.trusted_proxy_cidrs.split(",")
                if value.strip()
            ]
        except ValueError as exc:
            raise ValueError("trusted_proxy_cidrs contains an invalid CIDR") from exc
        # In production, reject broad RFC1918 ranges: they overlap the sandbox /
        # pod network, so trusting them lets an untrusted sandbox spoof
        # X-Forwarded-For (poisoning the signed audit actor_ip and bypassing
        # per-IP rate limiting). Non-production envs keep broad defaults so local
        # / test / e2e setups behind docker bridges are not disrupted.
        if self.env.lower() == "production":
            overbroad = [
                str(network)
                for network in trusted_proxy_networks
                if _is_overbroad_private_proxy_cidr(network)
            ]
            if overbroad:
                raise ValueError(
                    "trusted_proxy_cidrs must not include broad private ranges "
                    f"({', '.join(overbroad)}) in production: the sandbox / pod "
                    "network shares these ranges, so a compromised sandbox could "
                    "forge X-Forwarded-For. Set trusted_proxy_cidrs to the exact "
                    "ingress / reverse-proxy address(es) (a /32 host, or a narrow "
                    "/24-or-longer subnet)."
                )
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
