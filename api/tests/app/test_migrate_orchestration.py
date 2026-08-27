import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from core.config import DeploymentSettings


def test_migrate_main_applies_schema_before_runtime_policy_seed(monkeypatch):
    calls: list[str] = []
    original_run = asyncio.run

    class _FakeCommand:
        @staticmethod
        def upgrade(_cfg, _head):
            calls.append("schema")

    settings = DeploymentSettings(env="test")

    async def _seed(resolved_settings):
        assert resolved_settings is settings
        calls.append("seed")

    def _run(coro):
        return original_run(coro)

    @contextmanager
    def _migration_lock(resolved_settings):
        assert resolved_settings is settings
        calls.append("lock-enter")
        try:
            yield
        finally:
            calls.append("lock-exit")

    monkeypatch.setattr("app.migrate.command", _FakeCommand)
    monkeypatch.setattr("app.migrate.setup_logging", lambda resolved: None)
    monkeypatch.setattr("app.migrate.load_deployment_settings", lambda: settings)
    monkeypatch.setattr(
        "app.migrate.Config",
        lambda _path: SimpleNamespace(attributes={}),
    )
    monkeypatch.setattr("app.migrate.run_data_migrations", _seed)
    monkeypatch.setattr("app.migrate.asyncio.run", _run)
    monkeypatch.setattr("app.migrate.migration_lock", _migration_lock)

    from app.migrate import main

    main()

    assert calls == ["lock-enter", "schema", "seed", "lock-exit"]


def test_data_step_only_seeds_runtime_policy(monkeypatch):
    calls: list[str] = []

    settings = DeploymentSettings(env="test")

    async def _seed(resolved_settings):
        assert resolved_settings is settings
        calls.append("seed")
        return True

    monkeypatch.setattr("app.migrate.seed_runtime_policy_heads", _seed)

    from app.migrate import run_data_migrations

    asyncio.run(run_data_migrations(settings))

    assert calls == ["seed"]
