#!/usr/bin/env python
# -*- coding: utf-8 -*-
from core.config import Settings, sqlalchemy_sync_database_uri
import pytest


def test_settings_derives_database_uri_from_postgres_fields():
    settings = Settings(
        postgres_user="app",
        postgres_password="s3cret!",
        postgres_db="opencitadel",
        postgres_host="opencitadel-postgres",
    )
    assert settings.sqlalchemy_database_uri == (
        "postgresql+asyncpg://app:s3cret%21@opencitadel-postgres:5432/opencitadel"
    )


def test_settings_keeps_explicit_database_uri():
    explicit = "postgresql+asyncpg://custom:custom@db.example.com:5432/custom"
    settings = Settings(sqlalchemy_database_uri=explicit)
    assert settings.sqlalchemy_database_uri == explicit


def test_sqlalchemy_sync_database_uri_uses_postgres_fields():
    settings = Settings(
        postgres_user="app",
        postgres_password="s3cret!",
        postgres_db="opencitadel",
        postgres_host="opencitadel-postgres",
    )
    assert sqlalchemy_sync_database_uri(settings) == (
        "postgresql+psycopg2://app:s3cret%21@opencitadel-postgres:5432/opencitadel"
    )


def _production_settings(**updates):
    values = {
        "env": "production",
        "api_key_secret": "a" * 32,
        "audit_signing_key": "b" * 32,
        "jwt_secret": "c" * 32,
        "session_secret": "d" * 32,
        "cookie_secure": True,
        "bootstrap_admin_password": "strong-bootstrap-password",
        "postgres_password": "strong-postgres-password",
        "redis_password": "strong-redis-password",
    }
    values.update(updates)
    return Settings(**values)


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
