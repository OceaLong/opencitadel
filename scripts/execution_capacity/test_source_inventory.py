"""Actual inventory algorithms over substituted SQL/files; no runtime services."""

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.execution_capacity import persistence


def test_standard_final_total_excludes_other_owned_run_cohorts():
    class Result:
        def __init__(self, scoped):
            self.scoped = scoped

        def mappings(self):
            return self

        def one(self):
            return {
                "runs": 100000 if self.scoped else 100500,
                "events": 10000000 if self.scoped else 10050000,
                "hotspots": 10,
                "completed": True,
            }

    class Session:
        async def execute(self, sql, params):
            statement = str(sql)
            return Result(
                params == {"fixture": "fixture"}
                and "source_entity_type='capacity_fixture'" in statement
                and "source_entity_id=:fixture" in statement
            )

        async def scalar(self, sql, params):
            # A missing cohort predicate contaminates the formal total.
            statement = str(sql)
            return (
                10000000
                if "JOIN execution_run_projection" in statement
                and "p.source_entity_type='capacity_fixture'" in statement
                and "p.source_entity_id=:fixture" in statement
                and params == {"scope": "user:owner", "fixture": "fixture"}
                else 10050000
            )

    class Facts(persistence.PersistedFacts):
        @asynccontextmanager
        async def session(self):
            yield Session()

    facts = Facts(None, None, None, SimpleNamespace(user_id="owner"), None)
    asyncio.run(facts.standard_totals("fixture"))


def test_owned_set_canonicalization_rejects_duplicates_and_same_count_wrong_run():
    from scripts.execution_capacity.inventory import exact_owned_set

    assert exact_owned_set(["b", "a"], {"a", "b"}) == exact_owned_set(["a", "b"], {"a", "b"})
    for actual in (["a", "wrong"], ["a"], ["a", "b", "b"], ["a", "b", "extra"]):
        with pytest.raises(ValueError, match=r"source|parent|projector|configuration|proof|scope"):
            exact_owned_set(actual, {"a", "b"})


def test_attempt_inventory_includes_superseded_subjects_and_all_judges():
    from scripts.execution_capacity.inventory import batch_membership

    attempts = [
        {
            "batch_id": "batch",
            "scope_key": "scope",
            "run_id": "old",
            "result_id": "result",
            "attempt": 0,
            "intent": {"private": 1},
        },
        {
            "batch_id": "batch",
            "scope_key": "scope",
            "run_id": "current",
            "result_id": "result",
            "attempt": 1,
            "intent": {"private": 2},
        },
    ]
    judges = [
        {
            "batch_id": "batch",
            "scope_key": "scope",
            "run_id": "judge-old",
            "id": "j1",
            "result_id": "result",
        },
        {
            "batch_id": "batch",
            "scope_key": "scope",
            "run_id": "judge-new",
            "id": "j2",
            "result_id": "result",
        },
    ]
    result = batch_membership(attempts, judges, {"batch": "scope"})
    assert result == {
        "old": ("evaluation_subject", "scope", "batch"),
        "current": ("evaluation_subject", "scope", "batch"),
        "judge-old": ("evaluation_judge", "scope", "batch"),
        "judge-new": ("evaluation_judge", "scope", "batch"),
    }
    for bad in (
        [*attempts, attempts[0]],
        [dict(attempts[0], intent=None)],
        [dict(attempts[0], scope_key="foreign")],
    ):
        with pytest.raises(ValueError, match=r"source|parent|projector|configuration|proof|scope"):
            batch_membership(bad, judges, {"batch": "scope"})


def test_build_inventory_hashes_actual_frontend_dependencies_and_all_24_ddl(tmp_path):
    from scripts.execution_capacity.inventory import read_build_inventory

    groups = {
        "frontend": ["ui/app.js"],
        "dependencies": ["api/uv.lock", "ui/pnpm-lock.yaml"],
        "ddl": [f"api/alembic/versions/{i:04d}.py" for i in range(24)],
        "build": ["build.json"],
    }
    for paths in groups.values():
        for name in paths:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
    result = read_build_inventory(tmp_path, groups)
    assert result["files"]["ui/app.js"] == sha256(b"ui/app.js").hexdigest()
    assert len(result["groups"]["ddl"]) == 24
    (tmp_path / "ui/app.js").write_text("changed")
    assert read_build_inventory(tmp_path, groups)["digest"] != result["digest"]
    hidden = tmp_path / "api/alembic/versions/unlisted.py"
    hidden.write_text("unexpected migration")
    with pytest.raises(ValueError, match=r"DDL"):
        read_build_inventory(tmp_path, groups)
    groups["ddl"].pop()
    with pytest.raises(ValueError, match=r"24"):
        read_build_inventory(tmp_path, groups)


def test_projector_metadata_rejects_lag_and_incomplete_scope_or_generation():
    from scripts.execution_capacity.inventory import validate_projectors

    rows = [
        {
            "scope": "scope",
            "head": 8,
            "checkpoint": 8,
            "generation": "live",
            "source_version": 1,
            "algorithm_version": 1,
        }
    ]
    validate_projectors(rows, {"scope"})
    for bad in ([dict(rows[0], checkpoint=7)], [], [dict(rows[0], source_version=2)], rows + rows):
        with pytest.raises(ValueError, match=r"source|parent|projector|configuration|proof|scope"):
            validate_projectors(bad, {"scope"})


def test_read_failure_retains_error_and_never_exports_complete_inventory():
    from scripts.execution_capacity.inventory import SourceInventory

    result = SourceInventory()
    result.errors.append({"stage": "owners", "identity": "database", "error": "PermissionError"})
    with pytest.raises(ValueError, match=r"incomplete"):
        result.require_complete()
    assert result.safe()["errors"] == [
        {"stage": "owners", "identity": "database", "error": "PermissionError"}
    ]


def test_configuration_readback_verifies_signature_without_creating_journal_parent():
    from app.domain.models.execution_usage import content_revision
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )

    run = UUID(int=1)
    scope = OwnerScope.personal("owner")
    proof = DBPhysicalRequesterRepository(None, signing_secret="unit-secret")._seal(
        {
            "version": 1,
            "scope": "user:owner",
            "run_id": str(run),
            "kind": "user",
            "principal": {"user_id": "owner"},
        }
    )
    row = {
        "purpose": "evaluation_subject",
        "body": {"stage": "admission", "physical_requester": proof},
    }
    row["id"] = content_revision(dict(run_id=str(run), **row))

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [row]

    class Session:
        async def execute(self, *args):
            return Result()

    class Journal:
        def intent(self, *args):
            pytest.fail("read-only inventory mutated historical journal")

    class Facts(persistence.PersistedFacts):
        @asynccontextmanager
        async def session(self):
            yield Session()

    facts = Facts(None, None, Journal(), scope, None)
    assert (
        asyncio.run(
            facts.configuration(run, "unit-secret", purpose="evaluation_subject", record=False)
        )
        == row["id"]
    )
    row["body"]["physical_requester"]["signature"] = "0" * 64
    with pytest.raises(ValueError, match=r"source|parent|projector|configuration|proof|scope"):
        asyncio.run(
            facts.configuration(run, "unit-secret", purpose="evaluation_subject", record=False)
        )


def test_formal_replay_rejects_same_length_stale_projection():
    from scripts.execution_capacity.inventory_readback import replay_projection
    from scripts.test_benchmark_execution_visualization import _plan

    from app.domain.execution.events import StoredEvent
    from app.domain.execution.run import RunAggregate
    from app.domain.execution.serialization import canonical_state_hash
    from app.domain.execution.store import calculate_event_hash

    plan = _plan(1000)
    agg = RunAggregate()
    state = agg.initial_state(str(plan.run_id))
    events = []
    commands = list(plan.commands())
    for item in [commands[0], commands[1], commands[-1]]:
        command = item.bind().model_copy(update={"expected_stream_version": len(events)})
        decision = agg.decide(state, command)
        event = StoredEvent(
            **decision.events[0].model_dump(),
            position=len(events) + 1,
            event_id=UUID(int=len(events) + 1),
            stream_type="run",
            stream_id=str(plan.run_id),
            stream_version=len(events) + 1,
            owner_user_id=plan.owner_user_id,
            team_id=None,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            occurred_at=command.issued_at,
            prev_hash=events[-1].event_hash if events else "0" * 64,
            event_hash="0" * 64,
        )
        event = event.model_copy(update={"event_hash": calculate_event_hash(event)})
        events.append(event)
        state = agg.evolve(state, event)
    row = SimpleNamespace(
        stream_version=3,
        state_hash=canonical_state_hash(state),
        last_event_hash=events[-1].event_hash,
        terminal=True,
    )
    assert replay_projection(str(plan.run_id), events, row).status.value == "completed"
    row.state_hash = "0" * 64
    with pytest.raises(ValueError, match=r"projection"):
        replay_projection(str(plan.run_id), events, row)


def test_inventory_sql_reads_complete_sets_and_retains_failed_query_identity():
    from scripts.execution_capacity.inventory_sql import InventoryQueries

    class Result:
        def mappings(self):
            return self

        def one(self):
            return {
                "row_count": 2,
                "max_bytes": 20,
                "total_bytes": 40,
                "read_only": "on",
                "isolation": "repeatable read",
                "snapshot": "fixture:1",
            }

        async def __aiter__(self):
            for row in ({"stream_id": "one"}, {"stream_id": "two"}):
                yield row

        async def close(self):
            pass

    class Session:
        async def execute(self, sql, params):
            if params.get("fail"):
                raise PermissionError("private credential must not escape")
            return Result()

        async def stream(self, sql, params):
            return Result()

    async def exercise():
        query = InventoryQueries(Session())
        assert await query.rows("owners", "SELECT stream_id FROM execution_stream_owners", {}) == [
            {"stream_id": "one"},
            {"stream_id": "two"},
        ]
        with pytest.raises(PermissionError):
            await query.rows(
                "owners", "SELECT stream_id FROM execution_stream_owners", {"fail": True}
            )
        assert query.reads[0]["rows"] == 2
        assert query.reads[1]["error"] == "PermissionError"
        assert "private credential" not in str(query.reads)

    asyncio.run(exercise())


def test_batch_parent_readback_never_manufactures_missing_historical_parent():
    from scripts.execution_capacity.inventory_reader import ReadOnlyParents

    class Journal:
        def bounded_records(self, kind, budget):
            return (
                [("known", {"body": {"scope": "scope"}, "receipt": None})] if kind == "run" else []
            )

        def bounded_get(self, kind, key, budget):
            return (
                {"body": {"scope": "scope"}, "receipt": None}
                if kind == "run" and key == "known"
                else None
            )

    reader = ReadOnlyParents([Journal()])
    assert reader.parent("run", "known") == {"scope": "scope"}
    with pytest.raises(ValueError, match=r"parent"):
        reader.parent("run", "missing")
    with pytest.raises(ValueError, match=r"parent"):
        reader.intent("run", "missing", {"scope": "scope"})
    reader.intent("run", "known", {"scope": "scope"})
    with pytest.raises(ValueError, match=r"parent"):
        reader.intent("run", "known", {"scope": "foreign"})


def test_batch_fact_readback_returns_real_parent_without_creating_intent():
    from scripts.execution_capacity.batch_facts import BatchFacts

    run = UUID(int=4)
    batch = UUID(int=5)

    class Journal:
        def parent(self, kind, key):
            return {"scope": "user:owner"}

        def records(self, kind):
            return []

        def intent(self, *args):
            pytest.fail("read-only parent lookup wrote a new intent")

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        async def execute(self, sql, params):
            return (
                Result(
                    [
                        {
                            "result_id": UUID(int=6),
                            "attempt": 1,
                            "intent": {"real": "persisted"},
                            "case_revision_id": UUID(int=7),
                            "config_version_id": UUID(int=8),
                            "repetition": 0,
                        }
                    ]
                )
                if "evaluation_batch_attempts" in str(sql)
                else Result([])
            )

    class Facts(BatchFacts):
        @asynccontextmanager
        async def session(self):
            yield Session()

    from app.domain.models.scope import OwnerScope

    facts = Facts(None, None, Journal(), OwnerScope.personal("owner"), None, batch_id=batch)
    parent = asyncio.run(facts.own_run(run, record=False))
    assert parent["kind"] == "subject"
    assert parent["parent"]["attempt"] == 1
    assert parent["batch_id"] == str(batch)


def test_guest_inventory_boundary_enforces_phase_and_actual_round_identity():
    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity.guest_main import collect_inventory
    from scripts.execution_capacity.inventory import SourceInventory

    origin = SourceOrigin(kind="base", seal_id="seal", round=None, boot_id=None, clone_id=None)

    class Reader:
        def __init__(self):
            self.origin = origin

        async def read(self, *, base):
            return SourceInventory(reads_complete=True)

    reader = Reader()
    assert asyncio.run(collect_inventory(reader, phase="pre_seal")).reads_complete
    with pytest.raises(ValueError, match=r"phase"):
        asyncio.run(collect_inventory(reader, phase="cold_ready"))
    with pytest.raises(ValueError, match=r"round"):
        asyncio.run(collect_inventory(reader, phase="post_round"))


def test_result_inventory_retains_exact_current_result_and_run_identities():
    # Real paging/checking function; only the public service boundary is replaced.
    from scripts.execution_capacity.batch import result_inventory

    from app.domain.models.scope import OwnerScope, Principal

    cases = [UUID(int=i + 1) for i in range(1000)]
    configs = [UUID(int=i + 1001) for i in range(5)]

    class Item:
        def __init__(self, i, c, g):
            self.id = UUID(int=i + 2000)
            self.run_id = UUID(int=i + 10000)
            self.slot = SimpleNamespace(case_revision_id=c, config_version_id=g, repetition=0)
            self.execution_status = "succeeded"
            self.scoring_status = "complete"
            self.attempt = 0

        def model_dump_json(self):
            return str(self.id)

    items = [Item(i * 5 + j, c, g) for i, c in enumerate(cases) for j, g in enumerate(configs)]

    class Service:
        async def results(self, *args, cursor, limit):
            start = int(cursor or 0)
            end = start + limit
            return {
                "items": items[start:end],
                "next_cursor": str(end) if end < len(items) else None,
            }

    actual = asyncio.run(
        result_inventory(
            Service(),
            OwnerScope.personal("owner"),
            Principal(user_id="owner"),
            UUID(int=9),
            cases,
            configs,
            include_identities=True,
        )
    )
    assert actual["result_ids"] == sorted(str(x.id) for x in items)
    assert actual["run_ids"] == sorted(str(x.run_id) for x in items)


def test_collect_source_error_retains_full_identity_prefix_and_no_raw_secret(tmp_path):
    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity import inventory_reader as module
    from scripts.execution_capacity.inventory import SourceInventory
    from scripts.execution_capacity.inventory_reader import SourceInventoryReader

    from app.domain.models.authorization import AuthorizationContext

    class Query:
        def __init__(self):
            self.reads = []

        def finish_outputs(self):
            return self.reads

        async def rows(self, name, *args):
            if name == "database":
                return [
                    {
                        "database_name": "db",
                        "database_system_identifier": "123",
                        "migrations": ["migration"],
                        "read_only": "on",
                        "isolation": "repeatable read",
                    }
                ]
            if name == "owners":
                return [{"stream_type": "run", "stream_id": "retained-prefix"}]
            raise PermissionError("secret SQL credentials")

    async def fence():
        pass

    reader = SourceInventoryReader(
        sessions=None,
        authorization=AuthorizationContext.system("execution-kernel"),
        journals=[],
        binding={
            "environment": "test",
            "database_name": "db",
            "database_system_identifier": "123",
            "migration": "migration",
            "source_sha256": "source",
            "inventory_build_digest": "build",
        },
        seed=1,
        origin=SourceOrigin(kind="base", seal_id="seal", round=None, boot_id=None, clone_id=None),
        services={},
        storage=None,
        signing_secret="private",
        source_root=tmp_path,
        build_groups={},
        host_fence=fence,
    )
    result = SourceInventory()
    asyncio.run(module.collect_source(reader, Query(), result, ()))
    assert result.owners == [{"stream_type": "run", "stream_id": "retained-prefix"}]
    assert result.errors == [
        {"stage": "batch-attempts", "identity": "inventory", "error": "PermissionError"}
    ]
    assert result.reads_complete is False
    assert "secret SQL" not in str(result.safe())
    assert asyncio.run(reader.read()).errors == [
        {"stage": "authority", "identity": "inventory", "error": "ValueError"}
    ]


@pytest.mark.parametrize(
    ("index", "events", "steps"), [(0, 10000, 3331), (10, 100, 31), (1000, 99, 32)]
)
def test_standard_final_distribution_requires_completed_exact_source_and_steps(
    index, events, steps
):
    from scripts.execution_capacity.inventory_readback import validate_standard_counts

    fact = {"status": "completed", "formal_events": events, "visible_steps": steps}
    validate_standard_counts(index, fact)
    for key, value in (
        ("status", "failed"),
        ("formal_events", events + 1),
        ("visible_steps", steps + 1),
    ):
        with pytest.raises(ValueError, match="standard per-Run"):
            validate_standard_counts(index, {**fact, key: value})


@pytest.mark.parametrize("journal_window", ["window", "other-window", None])
def test_collect_source_binds_live_batch_to_original_journal_window(monkeypatch, journal_window):
    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity import inventory_reader as module
    from scripts.execution_capacity.inventory import SourceInventory
    from scripts.execution_capacity.test_inventory_contract import fixture

    from app.domain.models.authorization import AuthorizationContext

    parent = {"body": {"suite_version": "suite", "window_id": journal_window}, "receipt": None}

    class Journal:
        def bounded_records(self, kind, budget):
            return [("batch", parent)] if kind == "live_batch" else []

    class Query:
        reads = ()

        def finish_outputs(self):
            return self.reads

        async def rows(self, name, *args):
            return {
                "database": [
                    {
                        "database_name": "db",
                        "database_system_identifier": "123",
                        "migrations": ["migration"],
                        "read_only": "on",
                        "isolation": "repeatable read",
                    }
                ],
                "owners": [],
                "attempts": [],
                "judges": [],
                "batches": [{"id": "batch", "scope_key": "user:owner", "suite_version": "suite"}],
            }[name]

    async def fence():
        pass

    class VersionReadBoundary(RuntimeError):
        pass

    async def versions(*args):
        raise VersionReadBoundary

    reader = module.SourceInventoryReader(
        sessions=None,
        authorization=AuthorizationContext.system("execution-kernel"),
        journals=[Journal()],
        binding={
            "environment": "test",
            "database_name": "db",
            "database_system_identifier": "123",
            "migration": "migration",
            "source_sha256": "source",
            "inventory_build_digest": "build",
        },
        seed=1,
        origin=SourceOrigin.model_validate(fixture()[2]["origin"]),
        services={},
        storage=None,
        signing_secret="private",
        source_root=None,
        build_groups={},
        host_fence=fence,
    )
    monkeypatch.setattr(reader, "_versions", versions)
    result = SourceInventory()
    asyncio.run(module.collect_source(reader, Query(), result, ()))
    assert not result.reads_complete
    assert result.errors == [
        {
            "stage": "batch-attempts",
            "identity": "inventory",
            "error": "VersionReadBoundary" if journal_window == "window" else "ValueError",
        }
    ]
