#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace


def test_migrate_main_runs_alembic_before_llm_key_migration(monkeypatch):
    calls: list[str] = []
    orig_asyncio_run = asyncio.run

    class _FakeCommand:
        @staticmethod
        def upgrade(_cfg, _head):
            calls.append("alembic")

    async def _run_data_migrations():
        calls.append("data_migrations")

    def _run_async(coro):
        calls.append("asyncio_run")
        return orig_asyncio_run(coro)

    monkeypatch.setattr("app.migrate.command", _FakeCommand)
    monkeypatch.setattr("app.migrate.setup_logging", lambda: None)
    monkeypatch.setattr("app.migrate.Config", lambda _path: object())
    monkeypatch.setattr("app.migrate.run_data_migrations", _run_data_migrations)
    monkeypatch.setattr("app.migrate.asyncio.run", _run_async)

    from app.migrate import main

    main()

    assert calls == ["alembic", "asyncio_run", "data_migrations"]


def test_run_data_migrations_runs_all_steps_in_one_event_loop(monkeypatch):
    calls: list[str] = []

    async def _llm_keys():
        calls.append("llm_keys")
        return 0

    async def _seed():
        calls.append("seed")
        return False

    async def _mcp_a2a():
        calls.append("mcp_a2a")
        return {"mcp": 0, "a2a": 0}

    async def _mcp_secrets():
        calls.append("mcp_secrets")
        return {"urls": 0, "headers": 0, "env": 0}

    monkeypatch.setattr("app.migrate.migrate_legacy_plaintext_api_keys", _llm_keys)
    monkeypatch.setattr("app.migrate.seed_app_config_from_yaml_if_empty", _seed)
    monkeypatch.setattr("app.migrate.migrate_mcp_a2a_from_blob", _mcp_a2a)
    monkeypatch.setattr("app.migrate.migrate_mcp_url_and_secrets", _mcp_secrets)

    from app.migrate import run_data_migrations

    asyncio.run(run_data_migrations())

    assert calls == ["llm_keys", "seed", "mcp_a2a", "mcp_secrets"]


def test_seed_migration_shuts_down_postgres_on_early_return(monkeypatch):
    shutdown_called = {"value": False}

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args, **_kwargs):
            return _FakeResult()

    class _FakePostgres:
        async def init(self):
            return None

        async def shutdown(self):
            shutdown_called["value"] = True

        @property
        def session_factory(self):
            return lambda: _FakeSession()

    monkeypatch.setattr(
        "app.migrate_app_config_seed.get_settings",
        lambda: SimpleNamespace(app_config_filepath="config.yaml"),
    )
    monkeypatch.setattr("app.migrate_app_config_seed.get_postgres", lambda: _FakePostgres())

    from app.migrate_app_config_seed import seed_app_config_from_yaml_if_empty

    seeded = asyncio.run(seed_app_config_from_yaml_if_empty())

    assert seeded is False
    assert shutdown_called["value"] is True


def test_mcp_a2a_migration_shuts_down_postgres_on_early_return(monkeypatch):
    shutdown_called = {"value": False}

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args, **_kwargs):
            return _FakeResult()

    class _FakePostgres:
        async def init(self):
            return None

        async def shutdown(self):
            shutdown_called["value"] = True

        @property
        def session_factory(self):
            return lambda: _FakeSession()

    monkeypatch.setattr(
        "app.migrate_mcp_a2a_from_blob.get_settings",
        lambda: SimpleNamespace(api_key_secret="secret"),
    )
    monkeypatch.setattr("app.migrate_mcp_a2a_from_blob.get_postgres", lambda: _FakePostgres())
    monkeypatch.setattr(
        "app.migrate_mcp_a2a_from_blob.ApiKeyCipher",
        lambda _secret: SimpleNamespace(),
    )

    from app.migrate_mcp_a2a_from_blob import migrate_mcp_a2a_from_blob

    result = asyncio.run(migrate_mcp_a2a_from_blob())

    assert result == {"mcp": 0, "a2a": 0}
    assert shutdown_called["value"] is True


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
