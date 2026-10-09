"""Actual read_run/ORM acquisition and offline predicates over private fixture bytes."""

from datetime import UTC
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import DateTime, Integer, create_engine
from sqlalchemy.engine import Connection, IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.types import TypeDecorator


@pytest_asyncio.fixture
async def recorded_run(tmp_path, monkeypatch, request):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.inventory_readback import SnapshotFacts, read_run
    from scripts.execution_capacity.inventory_reader import ReadOnlyParents
    from scripts.execution_capacity.inventory_sql import InventoryQueries
    from scripts.execution_capacity.observer_session import ObserverSession
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.test_inventory_readback import completed_run

    from app.domain.execution.serialization import canonical_state_hash
    from app.domain.models.execution_usage import content_revision
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.models import ExecutionEventORM, ExecutionRunProjectionORM
    from app.infrastructure.models.execution_view import (
        ExecutionPlaybackCheckpointORM,
        ExecutionRunViewORM,
        ExecutionViewObservationORM,
    )
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )

    plan, state, events = completed_run()
    scope = OwnerScope.personal(plan.owner_user_id)
    secret = "fixture-private-hmac-material"
    signed = DBPhysicalRequesterRepository(None, signing_secret=secret)._seal(
        {
            "version": 1,
            "scope": "user:" + plan.owner_user_id,
            "run_id": str(plan.run_id),
            "kind": "user",
            "principal": {"user_id": plan.owner_user_id},
        }
    )
    config = {"body": {"stage": "admission", "physical_requester": signed}, "purpose": "production"}
    config["id"] = content_revision({"run_id": str(plan.run_id), **config})
    public = {
        "family": "ask",
        "status": "completed",
        "purpose": "production",
        "configuration": {"configuration_revision": config["id"]},
        "source": None,
        "wait_reason": None,
        "capabilities": [],
        "completeness": {"state": "complete", "missing_fields": [], "missing_intervals": []},
    }
    models = [
        ExecutionEventORM,
        ExecutionRunProjectionORM,
        ExecutionRunViewORM,
        ExecutionViewObservationORM,
        ExecutionPlaybackCheckpointORM,
    ]

    class UTCDateTime(TypeDecorator):
        impl = DateTime
        cache_ok = True

        def process_result_value(self, value, dialect):
            return value.replace(tzinfo=UTC) if value is not None else None

    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, DateTime):
                monkeypatch.setattr(column, "type", UTCDateTime())
    engine = create_engine("sqlite://")
    rows = [ExecutionEventORM(**event.model_dump()) for event in events]
    rows += [
        ExecutionRunProjectionORM(
            run_id=plan.run_id,
            stream_version=3,
            state_hash=canonical_state_hash(state),
            last_event_hash=events[-1].event_hash,
            terminal=True,
        )
    ]
    rows += [
        ExecutionRunViewORM(
            run_id=plan.run_id,
            scope_key="user:" + plan.owner_user_id,
            projector_version=1,
            formal_position=3,
            progress_position=0,
            observed_order=3,
            projection_revision=3,
            as_of=events[-1].occurred_at,
            completeness={},
        )
    ]
    rows += [
        ExecutionViewObservationORM(
            run_id=plan.run_id,
            scope_key="user:" + plan.owner_user_id,
            projector_version=1,
            formal_position=index + 1,
            progress_position=0,
            observed_order=index + 1,
            projection_revision=index + 1,
            observed_at=event.occurred_at,
            source_kind="formal",
            source_identity=str(event.event_id),
            public_payload={"facts": [{"kind": "run", "id": str(plan.run_id), "patch": public}]},
        )
        for index, event in enumerate(events)
    ]
    with engine.begin() as connection:
        for model in models:
            columns = ",".join(
                '"' + c.name + '" ' + ("INTEGER" if isinstance(c.type, Integer) else "TEXT")
                for c in model.__table__.columns
            )
            connection.exec_driver_sql(
                "CREATE TABLE " + model.__table__.name + " (" + columns + ")"
            )
        for row in rows:
            connection.execute(
                type(row).__table__.insert(),
                {c.name: getattr(row, c.name) for c in type(row).__table__.columns},
            )

    def result(values, names):
        return IteratorResult(
            SimpleResultMetaData(names),
            iter([tuple(row[name] for name in names) for row in values]),
        )

    generation = (
        "10000000-0000-0000-0000-000000000001"
        if getattr(request, "param", None) == "shadow"
        else None
    )
    source_mode = getattr(request, "param", None) == "source"
    source_rows = {}
    if source_mode:
        from scripts.execution_capacity.inventory_sql import (
            ATTEMPTS,
            IDENTITY,
            JUDGES,
            OWNERS,
            PROJECTORS,
        )

        source_rows = {
            IDENTITY: [
                {
                    "database_name": "fixture",
                    "database_user": "fixture",
                    "database_system_identifier": "42",
                    "snapshot": "fixture:run",
                    "read_only": "on",
                    "isolation": "repeatable read",
                    "server_version": "160000",
                    "migrations": ["fixture"],
                }
            ],
            OWNERS: [
                {
                    "stream_type": "run",
                    "stream_id": str(plan.run_id),
                    "owner_scope_key": "user:" + plan.owner_user_id,
                    "source_entity_type": "probe",
                    "source_entity_id": "probe",
                    "correlation_id": "fixture",
                    "stream_version": 3,
                    "terminal": True,
                }
            ],
            ATTEMPTS: [],
            JUDGES: [],
            PROJECTORS: [
                {
                    "scope": "user:" + plan.owner_user_id,
                    "head": 3,
                    "checkpoint": 3,
                    "generation": "live",
                    "source_version": 1,
                    "algorithm_version": 1,
                }
            ],
            "SELECT id,scope_key,suite_version,status FROM evaluation_batches ORDER BY scope_key,id": [],
        }
        for table, predicate in [
            ("execution_poisoned_runs", "TRUE"),
            ("execution_poisoned_scopes", "TRUE"),
            ("execution_recovery_requests", "status NOT IN ('completed')"),
            ("execution_view_generations", "status IN ('building','failed')"),
        ]:
            source_rows[f"SELECT * FROM {table} WHERE {predicate}"] = []
    original = Connection.execute

    def execute(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            # Read-only PG controls are injected; subsequent typed ORM queries
            # execute against this real private SQLite database.
            value = {
                "row_count": 1,
                "max_bytes": 64,
                "total_bytes": 64,
                "read_only": "on",
                "isolation": "repeatable read",
                "snapshot": "fixture:run",
            }
            sql = str(statement)
            if "FROM execution_events" in sql:
                value.update(row_count=3, total_bytes=192)
            if "FROM execution_view_checkpoints" in sql:
                value.update(row_count=0, max_bytes=0, total_bytes=0)
            if (
                "FROM (SELECT * FROM execution_view_shadow_steps" in sql
                or "FROM (SELECT * FROM execution_view_steps" in sql
                or "FROM (SELECT b.step_id" in sql
                or "FROM (SELECT * FROM execution_activity_tasks" in sql
            ):
                value.update(row_count=0, max_bytes=0, total_bytes=0)
            if "FROM (SELECT observed_order,projection_revision,source_kind" in sql:
                value.update(row_count=3, total_bytes=192)
            for query, values in source_rows.items():
                if "FROM (" + query + ") AS " in sql:
                    value.update(
                        row_count=len(values),
                        max_bytes=64 if values else 0,
                        total_bytes=64 * len(values),
                    )
            return result([value], list(value))
        if isinstance(statement, TextClause):
            sql = statement.text
            if sql in source_rows:
                values = source_rows[sql]
                return result(values, list(values[0]) if values else ["unused"])
            if sql.startswith("SELECT id,body,purpose FROM execution_configurations"):
                return result([config], ["id", "body", "purpose"])
            if sql.startswith("SELECT active_generation"):
                return result(
                    [] if generation is None else [{"active_generation": generation}],
                    ["active_generation"],
                )
            if sql.startswith("SELECT * FROM execution_view_shadow_runs"):
                import hashlib
                import json

                from app.application.execution.playback import reduce_facts
                from app.domain.models.playback import PlaybackBoundary

                boundary = PlaybackBoundary(
                    run_id=plan.run_id,
                    formal_position=3,
                    progress_position=0,
                    observed_order=3,
                    projection_revision=3,
                    observed_at=events[-1].occurred_at,
                    projector_version=1,
                )
                value = {
                    "generation": generation,
                    "run_id": plan.run_id,
                    "scope_key": "user:" + plan.owner_user_id,
                    "observed_order": 3,
                    "boundary": boundary.model_dump(mode="json"),
                    "coverage_token": hashlib.sha256(
                        json.dumps(
                            [hashlib.md5(b"1,2,3").hexdigest(), []], sort_keys=True, default=str
                        ).encode()
                    ).hexdigest(),
                    "missing_intervals": [],
                    "state": reduce_facts(
                        [
                            {
                                "kind": "run",
                                "id": str(plan.run_id),
                                "patch": public,
                                "position": (3, 0),
                                "observed_order": 3,
                            }
                        ],
                        boundary,
                    ),
                }
                return result([value], list(value))
            if sql.startswith("SELECT * FROM execution_view_shadow_steps"):
                return result([], ["unused"])
            if sql.startswith("SELECT f->>'id'"):
                return result([], ["step_id", "last_order"])
            if sql.startswith("SELECT count(*) AS count,"):
                import hashlib

                return result(
                    [{"count": 3, "digest": hashlib.md5(b"1,2,3").hexdigest()}], ["count", "digest"]
                )
            if sql.startswith("SELECT completeness->"):
                return result([{"value": []}], ["value"])
            if sql.startswith("SELECT state_ref->"):
                return result([], ["value"])
            if sql.startswith(
                (
                    "SELECT * FROM execution_view_steps",
                    "SELECT b.step_id",
                    "SELECT * FROM execution_activity_tasks",
                )
            ):
                return result([], ["unused"])
            if sql.startswith("SELECT observed_order,projection_revision,source_kind"):
                observations = [
                    {
                        "observed_order": i + 1,
                        "projection_revision": i + 1,
                        "source_kind": "formal",
                        "source_identity": str(e.event_id),
                        "projector_version": 1,
                    }
                    for i, e in enumerate(events)
                ]
                return result(observations, list(observations[0]))
            pytest.fail("unexpected source query: " + sql)
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", execute)
    owner = EvidenceOwner()

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    (tmp_path / "journal").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "journal") as journal:
        journal.intent("run", str(plan.run_id), {"scope": "user:" + plan.owner_user_id})
        parents = ReadOnlyParents((journal,), budget=owner.budget.child(), evidence=owner)
        objects = SimpleNamespace(originals=[])
        if source_mode:
            return await record_source(
                tmp_path, monkeypatch, owner, objects, journal, Bound, plan, secret, engine
            )
        async with AsyncSession(sync_session_class=Bound) as session:
            facts = SnapshotFacts(session, parents, scope, evidence=owner)
            actual = await read_run(
                InventoryQueries(session), facts, str(plan.run_id), "standard", secret, objects
            )
    engine.dispose()
    return owner, objects, actual, secret


@pytest.mark.asyncio
async def test_retained_run_replays_actual_acquired_hmac_and_empty_checkpoint(recorded_run):
    from scripts.execution_capacity import retained_run

    owner, objects, actual, secret = recorded_run
    inputs = retained_run.RetainedRunInputs(
        owner.originals["run-input"][0],
        owner.originals,
        owner.sql_reads,
        objects.originals,
        signing_secret=secret,
        budget=owner.budget,
    )
    assert await inputs.replay() == actual


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "wrong-secret",
        "missing-secret",
        "boundary",
        "boundary-absent",
        "boundary-error",
        "missing-empty",
        "extra-input",
        "params",
        "uow",
        "config-proof",
        "projection",
        "orders",
    ],
)
async def test_retained_run_rejects_original_semantic_mutations(recorded_run, fault):
    import copy

    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.retained_run import RetainedRunInputs

    from app.domain.models.execution_usage import content_revision

    owner, objects, _actual, secret = recorded_run
    operands, sql = copy.deepcopy(owner.originals), copy.deepcopy(owner.sql_reads)
    bundle = operands["run-input"][0]
    if fault == "wrong-secret":
        secret = "wrong-fixture-secret"
    elif fault == "missing-secret":
        secret = ""
    elif fault == "boundary":
        operands["playback-boundary-observation"][0]["row"]["formal_position"] = 2
    elif fault == "boundary-absent":
        operands["playback-boundary-observation"][0]["row"] = None
    elif fault == "boundary-error":
        operands["playback-boundary-observation"][0]["error"] = "RuntimeError"
    elif fault == "missing-empty":
        operands["playback-checkpoint"].clear()
        bundle["ranges"]["playback-checkpoint"][1] = 0
    elif fault == "extra-input":
        operands["playback-checkpoint"].append(None)
        bundle["ranges"]["playback-checkpoint"][1] += 1
    elif fault == "params":
        sql[0]["bound_parameters"]["stream_id_1"] = "other-run"
    elif fault == "uow":
        sql[1]["uow"] += 1
    elif fault == "config-proof":
        config = operands["signed-configuration"][0][0]
        config["body"]["physical_requester"]["proof"]["principal"]["user_id"] = "other-principal"
        config["id"] = content_revision(
            {
                "run_id": bundle["identity"]["run_id"],
                "purpose": config["purpose"],
                "body": config["body"],
            }
        )
    elif fault == "projection":
        operands["projection"][0]["state_hash"] = "0" * 64
    elif fault == "orders":
        operands["playback-orders"][0].remove(1)
    with pytest.raises((ValueError, KeyError)):  # noqa: PT012 - both construction and replay validate originals
        inputs = RetainedRunInputs(
            bundle,
            operands,
            sql,
            objects.originals,
            signing_secret=secret,
            budget=EvidenceOwner().budget,
        )
        await inputs.replay()


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded_run", ["shadow"], indirect=True)
async def test_retained_run_replays_actual_shadow_storage(recorded_run):
    from scripts.execution_capacity.retained_run import RetainedRunInputs

    owner, objects, actual, secret = recorded_run
    inputs = RetainedRunInputs(
        owner.originals["run-input"][0],
        owner.originals,
        owner.sql_reads,
        objects.originals,
        signing_secret=secret,
        budget=owner.budget,
    )
    assert await inputs.replay() == actual


async def record_source(
    tmp_path, monkeypatch, owner, objects, journal, Bound, plan, secret, engine
):
    from contextlib import asynccontextmanager

    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity import inventory_reader as reader
    from scripts.execution_capacity.host import source_digest
    from scripts.execution_capacity.inventory import read_build_inventory
    from scripts.execution_capacity.inventory_sql import InventoryQueries

    from app.domain.models.authorization import AuthorizationContext

    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    groups = {
        "ddl": [f"api/alembic/versions/{i}.py" for i in range(24)],
        "frontend": ["web.txt"],
        "dependencies": ["deps.txt"],
        "build": ["build.txt"],
    }
    names = [n for rows in groups.values() for n in rows] + [
        "scripts/seed_execution_visualization.py",
        "scripts/execution_capacity/compose.yml",
        "scripts/execution_capacity/compose.live.yml",
        "e2e/fixtures/inference-provider/server.mjs",
    ]
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    build = read_build_inventory(source, groups)
    binding = {
        "environment": "test",
        "source_sha256": source_digest(source),
        "inventory_build_digest": build["digest"],
        "database_name": "fixture",
        "database_system_identifier": "42",
        "migration": "fixture",
        "fixture_id": "00000000-0000-0000-0000-000000000010",
        "principal_id": plan.owner_user_id,
        "probe": {
            "fixture_id": "00000000-0000-0000-0000-000000000011",
            "principal_id": plan.owner_user_id,
        },
    }
    # Only fixed population enumeration/distribution is isolated. All owned
    # source parents, SQL, Run predicates, HMAC and playback execute unchanged.
    monkeypatch.setattr(reader, "standard_population", lambda *args: set())
    monkeypatch.setattr(reader, "probe_run_identity", lambda *args: str(plan.run_id))
    monkeypatch.setattr(reader.SourceInventoryReader, "_counts", lambda *args: None)

    @asynccontextmanager
    async def snapshot(*args):
        async with AsyncSession(sync_session_class=Bound) as session:
            yield InventoryQueries(session)

    monkeypatch.setattr(reader, "read_snapshot", snapshot)

    async def fence():
        pass

    origin = SourceOrigin(
        kind="base", seal_id="fixture-seal", round=None, clone_id=None, boot_id=None
    )
    live = reader.SourceInventoryReader(
        sessions=None,
        authorization=AuthorizationContext.system("execution-kernel"),
        journals=(journal,),
        binding=binding,
        seed=0,
        origin=origin,
        services={},
        storage=objects,
        signing_secret=secret,
        source_root=source,
        build_groups=groups,
        host_fence=fence,
        evidence=owner,
    )
    actual = await live.read()
    engine.dispose()
    assert actual.reads_complete, actual.errors
    return owner, objects, actual, secret, origin


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded_run", ["source"], indirect=True)
async def test_source_owner_to_finite_replay_connection_population_isolated(recorded_run):
    from scripts.execution_capacity.retained_source import OriginalTrace, RetainedSourceReader
    from scripts.execution_capacity.retained_versions import typed

    owner, objects, actual, secret, origin = recorded_run
    roots = {"operands": owner.originals, "sql": owner.sql_reads, "objects": objects.originals}
    trace = OriginalTrace(roots, budget=owner.budget)
    replay = RetainedSourceReader(
        trace, signing_secret=secret, cursor_secret=b"fixture-only-cursor-material"
    )
    restored = await replay.replay(typed(actual.__dict__), origin=origin)
    assert restored == actual
    assert trace.sql_position == len(owner.sql_reads)
    assert all(trace.positions[name] == len(rows) for name, rows in owner.originals.items())
