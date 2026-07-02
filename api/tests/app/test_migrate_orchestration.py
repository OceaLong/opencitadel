#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace


def test_migrate_main_runs_alembic_before_llm_key_migration(monkeypatch):
    calls: list[str] = []

    class _FakeCommand:
        @staticmethod
        def upgrade(_cfg, _head):
            calls.append("alembic")

    def _run_llm_migration(_coro):
        calls.append("llm_keys")
        return 2

    monkeypatch.setattr("app.migrate.command", _FakeCommand)
    monkeypatch.setattr("app.migrate.setup_logging", lambda: None)
    monkeypatch.setattr("app.migrate.Config", lambda _path: object())
    monkeypatch.setattr("app.migrate.asyncio.run", _run_llm_migration)

    from app.migrate import main

    main()

    assert calls == ["alembic", "llm_keys", "llm_keys"]


def test_migrate_legacy_plaintext_skips_cipher_when_no_candidates(monkeypatch):
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _FakePostgres:
        async def init(self):
            return None

        async def shutdown(self):
            return None

        @property
        def session_factory(self):
            return lambda: _FakeSession()

    async def _inspect(_session):
        from app.infrastructure.security.llm_key_inspector import LLMApiKeyInspectionReport

        return LLMApiKeyInspectionReport(
            total_models=0,
            empty_key_count=0,
            legacy_plaintext_count=0,
            fernet_v1_count=0,
            unknown_encryption_count=0,
            suspected_fernet_shape_count=0,
            suspected_plaintext_count=0,
        )

    cipher_called = {"value": False}

    def _cipher_ctor(_secret):
        cipher_called["value"] = True
        raise AssertionError("cipher should not be initialized")

    monkeypatch.setattr("app.migrate_llm_api_keys.get_settings", lambda: SimpleNamespace(env="production", api_key_secret=""))
    monkeypatch.setattr("app.migrate_llm_api_keys.get_postgres", lambda: _FakePostgres())
    monkeypatch.setattr("app.migrate_llm_api_keys.inspect_llm_api_keys", _inspect)
    async def _count_zero(_session):
        return 0

    monkeypatch.setattr("app.migrate_llm_api_keys.count_legacy_plaintext_models", _count_zero)
    monkeypatch.setattr("app.migrate_llm_api_keys.ApiKeyCipher", _cipher_ctor)

    import asyncio

    from app.migrate_llm_api_keys import migrate_legacy_plaintext_api_keys

    migrated = asyncio.run(migrate_legacy_plaintext_api_keys())

    assert migrated == 0
    assert cipher_called["value"] is False
