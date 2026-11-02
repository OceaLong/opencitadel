import pytest

import core.config as deployment_config
from core.config import (
    DeploymentSettings,
    sqlalchemy_sync_database_uri,
    sqlalchemy_sync_migration_database_uri,
)


@pytest.fixture(autouse=True)
def isolate_database_overrides(monkeypatch):
    # These unit tests exercise explicit fields and defaults, independently of
    # connection overrides used by the PostgreSQL integration suite.
    monkeypatch.setitem(DeploymentSettings.model_config, "env_file", None)
    for name in (*DeploymentSettings.model_fields, "OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


def test_settings_derives_database_uri_from_postgres_fields():
    settings = DeploymentSettings(
        postgres_user="app",
        postgres_password="s3cret!",
        postgres_db="opencitadel",
        postgres_host="opencitadel-postgres",
    )
    assert settings.sqlalchemy_database_uri == (
        "postgresql+asyncpg://app:s3cret%21@opencitadel-postgres:5432/opencitadel"
    )


def test_settings_metrics_defaults_are_fail_closed():
    """metrics_token empty -> /api/metrics stays 404 (see metrics_routes.py);
    execution_kernel_metrics_port 9108 -> the kernel binds a Prometheus
    scrape port by default."""
    settings = DeploymentSettings()

    assert settings.metrics_token == ""
    assert settings.execution_kernel_metrics_port == 9108


def test_bootstrap_admin_is_disabled_unless_explicitly_configured():
    settings = DeploymentSettings()

    assert settings.bootstrap_admin_email == ""
    assert settings.bootstrap_admin_password == ""


def test_settings_keeps_explicit_database_uri():
    explicit = "postgresql+asyncpg://custom:custom@db.example.com:5432/custom"
    settings = DeploymentSettings(sqlalchemy_database_uri=explicit)
    assert settings.sqlalchemy_database_uri == explicit


def test_sqlalchemy_sync_database_uri_uses_postgres_fields():
    settings = DeploymentSettings(
        postgres_user="app",
        postgres_password="s3cret!",
        postgres_db="opencitadel",
        postgres_host="opencitadel-postgres",
    )
    assert sqlalchemy_sync_database_uri(settings) == (
        "postgresql+psycopg2://app:s3cret%21@opencitadel-postgres:5432/opencitadel"
    )


def test_migration_database_uri_uses_distinct_admin_credentials():
    settings = DeploymentSettings(
        postgres_user="app",
        postgres_password="app-secret",
        postgres_admin_user="migration_admin",
        postgres_admin_password="admin-secret!",
        postgres_db="opencitadel",
        postgres_host="opencitadel-postgres",
    )

    assert sqlalchemy_sync_migration_database_uri(settings) == (
        "postgresql+psycopg2://migration_admin:admin-secret%21@"
        "opencitadel-postgres:5432/opencitadel"
    )


def test_production_migration_database_uri_requires_admin_credentials():
    settings = _production_settings(
        postgres_admin_user="postgres",
        postgres_admin_password="",
    )

    with pytest.raises(ValueError, match="migration database credentials"):
        sqlalchemy_sync_migration_database_uri(settings)


def _production_settings(**updates):
    values = {
        "env": "production",
        "api_key_secret": "a" * 32,
        "audit_signing_key": "b" * 32,
        "jwt_secret": "c" * 32,
        "session_secret": "d" * 32,
        "sandbox_token_seed": "e" * 32,
        "cookie_secure": True,
        "bootstrap_admin_password": "strong-bootstrap-password",
        "postgres_password": "strong-postgres-password",
        "redis_password": "strong-redis-password",
    }
    values.update(updates)
    return DeploymentSettings(**values)


def test_production_settings_require_distinct_audit_signing_key():
    with pytest.raises(ValueError, match="audit_signing_key"):
        _production_settings(audit_signing_key="a" * 32)


def test_production_settings_reject_default_database_credentials():
    with pytest.raises(ValueError, match="postgres_password"):
        _production_settings(postgres_password="postgres")
    with pytest.raises(ValueError, match="redis_password"):
        _production_settings(redis_password="")


def test_production_does_not_require_a_disabled_bootstrap_account():
    settings = _production_settings(bootstrap_admin_email="", bootstrap_admin_password="")

    assert settings.bootstrap_admin_email == ""


def test_production_bootstrap_requires_a_strong_password_when_enabled():
    with pytest.raises(ValueError, match="bootstrap_admin_password"):
        _production_settings(bootstrap_admin_email="admin@example.com", bootstrap_admin_password="")


def test_production_validates_the_password_in_an_explicit_database_uri():
    settings = _production_settings(
        postgres_password="postgres",
        sqlalchemy_database_uri=(
            "postgresql+asyncpg://external:external-database-password@db.example:5432/external"
        ),
    )

    assert settings.postgres_password == "postgres"
    assert "@db.example:5432/external" in settings.sqlalchemy_database_uri


@pytest.mark.parametrize("password", ["", "postgres", "too-short", "replace-with-db-password"])
def test_production_rejects_weak_passwords_in_an_explicit_database_uri(password):
    with pytest.raises(ValueError, match="postgres_password"):
        _production_settings(
            sqlalchemy_database_uri=f"postgresql+asyncpg://external:{password}@db:5432/external"
        )


def test_production_settings_accept_strong_distinct_secrets():
    settings = _production_settings()

    assert settings.audit_signing_key_id == "primary"


@pytest.mark.parametrize(
    "cidrs",
    [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "10.20.0.0/16",
        "127.0.0.1/32,10.0.0.0/8",
    ],
)
def test_production_rejects_overbroad_trusted_proxy_cidrs(cidrs):
    with pytest.raises(ValueError, match="trusted_proxy_cidrs"):
        _production_settings(trusted_proxy_cidrs=cidrs)


@pytest.mark.parametrize(
    "cidrs",
    [
        "127.0.0.1/32,::1/128",
        "10.1.2.3/32",
        "172.16.5.0/24",
        "192.168.1.0/26",
        "203.0.113.7/32",
    ],
)
def test_production_accepts_narrow_trusted_proxy_cidrs(cidrs):
    settings = _production_settings(trusted_proxy_cidrs=cidrs)

    assert settings.trusted_proxy_cidrs == cidrs


def test_non_production_allows_broad_trusted_proxy_cidrs():
    # local / test / e2e keep broad docker-bridge ranges without being disrupted.
    settings = DeploymentSettings(env="development", trusted_proxy_cidrs="10.0.0.0/8")

    assert settings.trusted_proxy_cidrs == "10.0.0.0/8"


def test_production_still_rejects_invalid_trusted_proxy_cidr():
    with pytest.raises(ValueError, match="invalid CIDR"):
        _production_settings(trusted_proxy_cidrs="not-a-cidr")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_key_secret", "replace-with-a-real-api-key-secret-123"),
        ("audit_signing_key", "change-me-audit-signing-key-value-123"),
        ("bootstrap_admin_password", "replace-with-admin-password"),
        ("postgres_password", "replace-with-postgres-password"),
        ("redis_password", "replace-with-redis-password"),
    ],
)
def test_production_settings_reject_placeholder_credentials(field, value):
    if field == "bootstrap_admin_password":
        with pytest.raises(ValueError, match=field):
            _production_settings(bootstrap_admin_email="admin@example.com", **{field: value})
        return
    with pytest.raises(ValueError, match=field):
        _production_settings(**{field: value})


def test_production_minio_rejects_default_credentials():
    with pytest.raises(ValueError, match="minio_access_key"):
        _production_settings(storage_provider="minio")
    with pytest.raises(ValueError, match="minio_secret_key"):
        _production_settings(
            storage_provider="minio",
            minio_access_key="real-minio-access",
        )
    with pytest.raises(ValueError, match="minio_access_key"):
        _production_settings(storage_provider="minio", minio_access_key="")


def test_production_minio_rejects_placeholder_credentials():
    with pytest.raises(ValueError, match="minio_secret_key"):
        _production_settings(
            storage_provider="minio",
            minio_access_key="real-minio-access",
            minio_secret_key="change-me-minio-secret",
        )


def test_production_minio_accepts_real_credentials():
    settings = _production_settings(
        storage_provider="minio",
        minio_access_key="real-minio-access",
        minio_secret_key="real-minio-secret",
    )

    assert settings.storage_provider == "minio"


def test_production_cos_ignores_minio_defaults():
    # COS-backed production leaves the bundled MinIO defaults untouched.
    settings = _production_settings(storage_provider="cos")

    assert settings.minio_access_key == "minioadmin"


def test_db_authorization_signing_secret_defaults_to_session_secret() -> None:
    # Unset => falls back to session_secret so existing deployments (and the RLS
    # values seeded from app.rls_signing_secret = session_secret) keep working.
    settings = DeploymentSettings(session_secret="a-session-secret-value")

    assert settings.database_authorization_signing_secret == "a-session-secret-value"


def test_db_authorization_signing_secret_can_be_configured_independently() -> None:
    settings = DeploymentSettings(
        session_secret="a-session-secret-value",
        database_authorization_signing_secret="a-distinct-db-auth-signing-secret",
    )

    assert settings.database_authorization_signing_secret == "a-distinct-db-auth-signing-secret"


def test_production_db_auth_signing_secret_may_mirror_session_secret() -> None:
    # Leaving it unset in production keeps it equal to session_secret without
    # tripping the distinctness guard (the trust-domain split is opt-in).
    settings = _production_settings()

    assert settings.database_authorization_signing_secret == settings.session_secret


def test_production_db_auth_signing_secret_rejects_weak_explicit_value() -> None:
    with pytest.raises(ValueError, match="database_authorization_signing_secret"):
        _production_settings(database_authorization_signing_secret="too-short")


def test_production_db_auth_signing_secret_rejects_placeholder_explicit_value() -> None:
    with pytest.raises(ValueError, match="database_authorization_signing_secret"):
        _production_settings(
            database_authorization_signing_secret="change-me-db-auth-signing-secret-123"
        )


def test_production_db_auth_signing_secret_must_be_distinct_when_set() -> None:
    with pytest.raises(ValueError, match="database_authorization_signing_secret"):
        _production_settings(database_authorization_signing_secret="c" * 32)


def test_production_db_auth_signing_secret_accepts_strong_distinct_value() -> None:
    settings = _production_settings(database_authorization_signing_secret="z" * 40)

    assert settings.database_authorization_signing_secret == "z" * 40


def test_deployment_settings_reject_invalid_policy_freshness() -> None:
    deployment_settings = deployment_config.DeploymentSettings

    with pytest.raises(ValueError, match="policy_max_staleness_seconds"):
        deployment_settings(
            policy_head_refresh_interval_seconds=30,
            policy_max_staleness_seconds=30,
        )


def test_deployment_settings_read_the_shared_shutdown_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS", "17.5")

    settings = deployment_config.DeploymentSettings(_env_file=None)

    assert settings.shutdown_timeout_seconds == 17.5


def test_deployment_settings_own_observability_bootstrap() -> None:
    settings = deployment_config.DeploymentSettings()

    # Empty by default: with allow_credentials the wildcard is folded to "no
    # cross-origin allowed" anyway, so the default now states that directly.
    assert settings.cors_origins == ""
    assert settings.otel_enabled is False
    assert settings.otel_service_name == "opencitadel-api"
    assert settings.otel_exporter_endpoint == ""


def test_deployment_settings_own_sandbox_topology() -> None:
    settings = deployment_config.DeploymentSettings()

    assert settings.sandbox_driver == "auto"
    assert settings.sandbox_image == ""
    assert settings.sandbox_network == ""
    assert settings.sandbox_k8s_namespace == "default"
    assert settings.policy_head_refresh_interval_seconds == 5.0
    assert settings.policy_max_staleness_seconds == 30.0


@pytest.mark.parametrize(
    ("origin", "secure"),
    [
        ("http://localhost:8088", False),
        ("https://app.example.com/", True),
        ("http://[::1]:9000", False),
    ],
)
def test_public_origin_derives_cookie_and_oauth_defaults(origin, secure):
    settings = DeploymentSettings(frontend_base_url=origin)

    assert settings.frontend_base_url == origin.rstrip("/")
    assert settings.oauth_redirect_base == f"{origin.rstrip('/')}/api/auth/oauth"
    assert settings.cookie_secure is secure


def test_explicit_cookie_and_oauth_overrides_remain_effective(monkeypatch):
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("OAUTH_REDIRECT_BASE", "https://auth.example.com/api/auth/oauth")

    settings = DeploymentSettings()

    assert settings.cookie_secure is False
    assert settings.oauth_redirect_base == "https://auth.example.com/api/auth/oauth"


def test_empty_cookie_override_uses_the_site_scheme():
    settings = DeploymentSettings(frontend_base_url="https://app.example.com", cookie_secure="")

    assert settings.cookie_secure is True


@pytest.mark.parametrize(
    "origin",
    [
        "app.example.com",
        "ftp://app.example.com",
        "http://example.com/path",
        "https://example.com?a=1",
        "https://user:password@example.com",
        "http://localhost:99999",
        "http://invalid host.example.com",
        "https://:80",
        "https://invalid<host>.example.com",
        "https://invalid\\host.example.com",
        "https://example.com;invalid",
    ],
)
def test_public_origin_rejects_ambiguous_or_unsafe_values(origin):
    with pytest.raises(ValueError, match="frontend_base_url"):
        DeploymentSettings(frontend_base_url=origin)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("env", "prod"),
        ("storage_provider", "typo"),
        ("sandbox_driver", "typo"),
        ("execution_activity_max_concurrency", 0),
        ("postgres_pool_size", 0),
        ("postgres_max_overflow", -1),
    ],
)
def test_invalid_deployment_choices_and_resource_limits_fail_early(field, value):
    with pytest.raises(ValueError, match=field):
        DeploymentSettings(**{field: value})


def test_deployment_choices_accept_existing_case_insensitive_overrides():
    settings = DeploymentSettings(env=" TEST ", storage_provider="MINIO", sandbox_driver="DOCKER")

    assert (settings.env, settings.storage_provider, settings.sandbox_driver) == (
        "test",
        "minio",
        "docker",
    )


def test_production_rejects_weak_explicit_cursor_secrets():
    with pytest.raises(ValueError, match="public_cursor_secret"):
        _production_settings(public_cursor_secret="x")


def test_production_https_site_derives_secure_cookies():
    settings = _production_settings(frontend_base_url="https://app.example.com", cookie_secure=None)

    assert settings.cookie_secure is True


def test_production_http_site_does_not_silently_disable_secure_cookie_requirement():
    with pytest.raises(ValueError, match="cookie_secure"):
        _production_settings(frontend_base_url="http://app.example.com", cookie_secure=None)
