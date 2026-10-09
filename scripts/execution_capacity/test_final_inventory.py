"""Final observer lifetime and fail-closed acquisition; no external effects."""

import asyncio
from types import SimpleNamespace

import pytest
from scripts.execution_capacity import final_inventory as source


def test_final_resource_entry_requires_original_backed_observer_factory(tmp_path):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.observer_session import ObserverSession

    from app.domain.models.authorization import AuthorizationContext

    budget = EvidenceBudget(bytes_limit=32 * 1024 * 1024, rows_limit=65_536)
    plain = EvidenceOwner(budget=budget)

    def build(owner, options):
        resources = SimpleNamespace(
            evidence=owner,
            postgres=SimpleNamespace(session_factory=SimpleNamespace(kw=options)),
            evidence_objects=object(),
            object_storage_client=object(),
            evidence_transport=object(),
        )
        return source.FinalInventory.from_resources(
            writers=object(),
            resources=resources,
            authorization=AuthorizationContext.system("execution-kernel"),
            journals=(),
            output_journal=object(),
            binding={"environment": "test"},
            seed=0,
            origin=None,
            services={},
            signing_secret="private",
            source_root=tmp_path,
            build_groups=(),
            host_fence=lambda: None,
            docker=object(),
        )

    with pytest.raises(ValueError, match="original-backed observer"):
        build(plain, {"sync_session_class": ObserverSession, "budget": budget, "evidence": plain})
    root = tmp_path / "originals"
    with EvidenceOwner(budget=budget, original_root=root, index_bytes=4 * 1024 * 1024) as owner:
        owner.begin_cleanup()
        with pytest.raises(ValueError, match="original-backed observer"):
            build(owner, {"sync_session_class": ObserverSession, "budget": budget})
        final = build(
            owner,
            {"sync_session_class": ObserverSession, "budget": budget, "evidence": owner},
        )
        assert final.reader.evidence is owner


def test_observer_resources_never_use_mutating_postgres_or_writer_factory(tmp_path, monkeypatch):
    from scripts.execution_capacity import observer_resources as module

    calls = []

    class Engine:
        async def dispose(self):
            calls.append("dispose")

    def engine(url, **options):
        assert options["execution_options"]["postgresql_readonly"] is True
        calls.append("engine")
        return Engine()

    class Storage:
        def __init__(self, settings, *, real_test_io):
            assert real_test_io

        async def init(self):
            calls.append("minio-read-client")

        async def shutdown(self):
            calls.append("minio-close")

    monkeypatch.setattr(module, "create_async_engine", engine)
    monkeypatch.setattr(
        module, "async_sessionmaker", lambda *args, **kwargs: SimpleNamespace(options=kwargs)
    )
    monkeypatch.setattr(module, "Minio", Storage)
    settings = SimpleNamespace(
        env="test",
        storage_provider="minio",
        minio_endpoint="owned",
        minio_bucket="bucket",
        sqlalchemy_database_uri="private",
        database_authorization_signing_secret="private",
    )
    binding = {"environment": "test", "minio_endpoint": "owned", "minio_bucket": "bucket"}

    async def run():
        async with module.open_observers(settings, binding, evidence_owner=owner) as value:
            assert (
                value.postgres.session_factory.options["info"][
                    "database_authorization_signing_secret"
                ]
                == "private"
            )
            from scripts.execution_capacity.evidence_objects import EvidenceObjects
            from scripts.execution_capacity.observer_session import ObserverSession

            assert value.postgres.session_factory.options["sync_session_class"] is ObserverSession
            assert value.postgres.session_factory.options["budget"] is value.evidence.budget
            assert isinstance(value.evidence_objects, EvidenceObjects)
            calls.append("read")
        assert value.closed_ns > value.opened_ns

    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    with EvidenceOwner(original_root=tmp_path / "c2c-originals", index_bytes=64 * 1024) as owner:
        asyncio.run(run())
    assert calls == ["engine", "minio-read-client", "read", "minio-close", "dispose"]


def test_after_exit_final_reader_keeps_source_failure_and_still_reads_storage_broker(monkeypatch):
    calls = []

    class Writers:
        def final_journals(self):
            calls.append("actual-exits")
            return {"issues": []}

    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    class Reader:
        evidence = EvidenceOwner()
        origin = SourceOrigin(
            kind="base", seal_id="fixture", round=None, boot_id=None, clone_id=None
        )
        storage = SimpleNamespace(originals=[])

        async def read(self, **kwargs):
            calls.append("source")
            raise ValueError("lost source")

    class StorageReader:
        def __init__(self, client, bucket, *, budget=None):
            pass

        def read(self):
            calls.append("storage")
            return SimpleNamespace(objects=[], uploads=[], pages=[])

    monkeypatch.setattr(source, "MinioInventory", StorageReader)

    def broker(*args, **kwargs):
        calls.append("broker")
        raise ValueError("lost broker")

    monkeypatch.setattr(source, "read_broker", broker)
    final = source.FinalInventory(
        writers=Writers(),
        reader=Reader(),
        journal=None,
        storage=SimpleNamespace(client=None, bucket="owned"),
        binding={},
        docker=None,
    )
    value = asyncio.run(final.read())
    assert calls == ["actual-exits", "source", "storage", "broker"]
    assert not value["complete"]
    assert {r["kind"] for r in value["issues"]} == {"source", "objects", "broker"}


def test_retained_upload_and_live_dispatch_history_cannot_be_replaced(tmp_path):
    from scripts.execution_capacity.observers import RecoveryJournal

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("object", "key", {"size": 3, "sha256": "a" * 64})
        journal.intent("upload", "port", {"key": "key", "size": 3, "sha256": "a" * 64})
        journal.acknowledge("upload", "port", {"size": 3, "sha256": "a" * 64})
        journal.acknowledge("object", "key", {"size": 3, "sha256": "a" * 64})
        journal.intent(
            "live_disposition",
            "history",
            {
                "runs": ["run"],
                "dispatches": [
                    {
                        "run_id": "run",
                        "call_identity": "call",
                        "state": "settled",
                        "settlement": {"tokens": 2},
                        "fact": {"tokens": 2},
                    }
                ],
                "outcomes": {"run": "completed"},
            },
        )
        uploads = {
            "sdk": {
                "body": {"port_upload_id": "port", "key": "key", "size": 3, "sha256": "a" * 64},
                "receipt": {"size": 3, "sha256": "a" * 64},
            }
        }
        rows = {
            "execution_model_dispatches": [
                {"run_id": "run", "scope_key": "user:owner", "call_identity": "call"}
            ],
            "evaluation_budget_reservations": [
                {
                    "scope_key": "user:owner",
                    "call_identity": "call",
                    "state": "settled",
                    "settlement": {"tokens": 2},
                }
            ],
            "execution_model_settlements": [
                {"scope_key": "user:owner", "call_identity": "call", "fact": {"tokens": 2}}
            ],
            "execution_run_projection": [{"run_id": "run", "status": "completed"}],
        }
        inventory = SimpleNamespace(
            objects=[{"key": "key", "size_bytes": 3, "sha256": "a" * 64}],
            owners=[{"stream_id": "run", "owner_scope_key": "user:owner"}],
        )
        assert not source.retained_history(journal, uploads, rows, inventory)["issues"]
        assert source.retained_history(journal, {}, rows, inventory)["issues"]
        rows["execution_model_settlements"][0]["fact"] = {"tokens": 4}
        assert source.retained_history(journal, uploads, rows, inventory)["issues"]


@pytest.mark.parametrize(
    "fault",
    [None, "missing_port_ledger", "missing_writer", "incomplete_storage", "physical_present"],
)
def test_concrete_final_constructor_joins_retained_journals_and_actual_read_boundaries(
    tmp_path, monkeypatch, fault
):
    from scripts.execution_capacity.test_final_connection_fixture import acquire_final

    value = asyncio.run(acquire_final(tmp_path, monkeypatch, fault=fault))
    result = value.final
    calls = value.transport_calls
    assert not any(row.get("type") == "ProgrammingError" for row in result["issues"])
    assert result["complete"] == (fault is None)
    assert any(call[0] == "container" for call in calls)
    assert set(result["predicate_journals"]) == {
        "lease",
        "lease_state",
        "environment_observation",
        "environment_read",
    }
    assert result["predicate_journals"]["lease"]
    if fault is None:
        assert result["physical"]["retained_resources"] == 0
    if fault == "physical_present":
        assert len(result["physical_observations"]) == 2
        assert all(
            row["response"] == "retained-resource" for row in result["physical_observations"]
        )
    assert {"source", "storage", "broker"} <= set(result["reads"])
