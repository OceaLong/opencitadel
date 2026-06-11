#!/usr/bin/env python
# -*- coding: utf-8 -*-
from core.config import Settings


def test_settings_derives_database_uri_from_postgres_fields():
    settings = Settings(
        postgres_user="app",
        postgres_password="s3cret!",
        postgres_db="manus",
        postgres_host="manus-postgres",
    )
    assert settings.sqlalchemy_database_uri == (
        "postgresql+asyncpg://app:s3cret%21@manus-postgres:5432/manus"
    )


def test_settings_keeps_explicit_database_uri():
    explicit = "postgresql+asyncpg://custom:custom@db.example.com:5432/custom"
    settings = Settings(sqlalchemy_database_uri=explicit)
    assert settings.sqlalchemy_database_uri == explicit
