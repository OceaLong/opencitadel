#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio

from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


def test_migrate_mcp_url_and_secrets_encrypts_legacy_rows(monkeypatch):
    class _FakeRecord:
        def __init__(self):
            self.id = "srv-1"
            self.url = "https://mcp.amap.com/mcp?key=3244242424"
            self.url_encryption = ApiKeyEncryption.LEGACY_PLAINTEXT
            self.headers = {"Authorization": "Bearer secret-token"}
            self.headers_encryption = ApiKeyEncryption.LEGACY_PLAINTEXT
            self.env = {"QINIU_ACCESS_KEY": "ak1234567890"}
            self.env_encryption = ApiKeyEncryption.LEGACY_PLAINTEXT
            self.created_at = None

    class _FakeScalars:
        def __init__(self, records):
            self.records = records

        def all(self):
            return self.records

    class _FakeResult:
        def __init__(self, records):
            self.records = records

        def scalars(self):
            return _FakeScalars(self.records)

    class _FakeSession:
        committed = False
        rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args, **_kwargs):
            return _FakeResult([_FakeRecord()])

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    class _FakePostgres:
        def __init__(self):
            self.session = _FakeSession()

        async def init(self):
            return None

        async def shutdown(self):
            return None

        @property
        def session_factory(self):
            return lambda: self.session

    fake_postgres = _FakePostgres()
    monkeypatch.setattr(
        "app.migrate_mcp_url_and_secrets.get_settings",
        lambda: type("Settings", (), {"api_key_secret": "test-secret-key-for-unit-tests-only"})(),
    )
    monkeypatch.setattr("app.migrate_mcp_url_and_secrets.get_postgres", lambda: fake_postgres)

    from app.migrate_mcp_url_and_secrets import migrate_mcp_url_and_secrets

    summary = asyncio.run(migrate_mcp_url_and_secrets())

    assert summary == {"urls": 1, "headers": 1, "env": 1}
    assert fake_postgres.session.committed is True
