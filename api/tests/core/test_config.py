import pytest

import core.config as deployment_config
from core.config import (
    DeploymentSettings,
    sqlalchemy_sync_database_uri,
    sqlalchemy_sync_migration_database_uri,
)


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
