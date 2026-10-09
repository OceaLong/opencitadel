"""Test-only actual observer acquisition fixture; never runtime configuration."""

import json
import sqlite3
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid5

from sqlalchemy import DateTime, Integer, create_engine
from sqlalchemy.engine import Connection, IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.types import TypeDecorator


async def acquire_final(
    tmp_path,
    monkeypatch,
    *,
    origin=None,
    base_value=None,
    round_owners=None,
    budget=None,
    fault=None,
    diagnostics=(),
    original_root=None,
    index_bytes=None,
):
    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity import batch, batch_facts, cumulative_cleanup, inventory_reader
    from scripts.execution_capacity.broker_inventory import (
        request_identity,
        sqlite_pages,
    )
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.evidence_objects import EvidenceObjects
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.final_inventory import CumulativeJournal, FinalInventory
    from scripts.execution_capacity.host import source_digest
    from scripts.execution_capacity.inventory import read_build_inventory
    from scripts.execution_capacity.inventory_sql import (
        ATTEMPTS,
        IDENTITY,
        JUDGES,
        OWNERS,
        PROJECTORS,
        InventoryQueries,
    )
    from scripts.execution_capacity.observer_session import ObserverSession
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.retained_versions import typed
    from scripts.execution_capacity.seal_cleanup import version_services
    from scripts.execution_capacity.test_inventory_readback import completed_run
    from scripts.execution_capacity.test_storage_inventory import (
        SDK,
        uploads,
    )
    from scripts.execution_capacity.test_storage_inventory import (
        objects as object_page,
    )
    from scripts.execution_capacity.test_writer_replay import bounded_inspect_fixture, inputs
    from scripts.execution_capacity.writer_lifecycle import ContainerWriters

    from app.domain.evaluation.configuration import (
        ConfigVersion,
        SuiteSettings,
        SuiteVersion,
        digest,
    )
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.environment import EnvironmentLease, EnvironmentVersion
    from app.domain.evaluation.rubric import RubricVersion
    from app.domain.execution.serialization import canonical_state_hash
    from app.domain.execution.store import calculate_event_hash
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.execution_usage import content_revision
    from app.infrastructure.execution.models import ExecutionEventORM, ExecutionRunProjectionORM
    from app.infrastructure.models.execution_view import (
        ExecutionPlaybackCheckpointORM,
        ExecutionRunViewORM,
        ExecutionViewObservationORM,
    )
    from app.infrastructure.models.user import UserORM
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )
    from app.infrastructure.security.db_authorization import _AUTHORIZATION_SQL

    budget = (
        budget
        if budget is not None
        else EvidenceBudget(bytes_limit=16 * 1024**3, rows_limit=10_000_000, row_limit=4 * 1024**2)
    )
    owner = EvidenceOwner(budget=budget, original_root=original_root, index_bytes=index_bytes)
    owner.begin_cleanup()
    secret = "fixture-original-requester"
    cursor = b"fixture-original-cursor"
    probe, pstate, pevents = completed_run()
    subject, sstate, sevents = completed_run(1001)
    # Distinct global event identities/positions preserve real hash-before-upcast.
    changed = []
    for index, event in enumerate(sevents):
        row = event.model_copy(
            update={
                "position": index + 4,
                "event_id": UUID(int=index + 4),
                "prev_hash": changed[-1].event_hash if changed else "0" * 64,
                "event_hash": "0" * 64,
            }
        )
        changed.append(row.model_copy(update={"event_hash": calculate_event_hash(row)}))
    sevents = changed
    from app.domain.execution.run import RunAggregate

    aggregate = RunAggregate()
    sstate = aggregate.initial_state(str(subject.run_id))
    for event in sevents:
        sstate = aggregate.evolve(sstate, event)
    uid = probe.owner_user_id
    scope = "user:" + uid
    models = [
        ExecutionEventORM,
        ExecutionRunProjectionORM,
        ExecutionRunViewORM,
        ExecutionViewObservationORM,
        ExecutionPlaybackCheckpointORM,
        UserORM,
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
    rows = []
    configs = {}
    event_sets = {}
    for plan, state, events, purpose in (
        (probe, pstate, pevents, "production"),
        (subject, sstate, sevents, "evaluation_subject"),
    ):
        run = str(plan.run_id)
        event_sets[run] = events
        proof = DBPhysicalRequesterRepository(None, signing_secret=secret)._seal(
            {
                "version": 1,
                "scope": scope,
                "run_id": run,
                "kind": "user",
                "principal": {"user_id": uid},
            }
        )
        config = {"body": {"stage": "admission", "physical_requester": proof}, "purpose": purpose}
        config["id"] = content_revision({"run_id": run, **config})
        configs[run] = config
        public = {
            "family": "ask",
            "status": "completed",
            "purpose": purpose,
            "configuration": {"configuration_revision": config["id"]},
            "source": None,
            "wait_reason": None,
            "capabilities": [],
            "completeness": {"state": "complete", "missing_fields": [], "missing_intervals": []},
        }
        rows.extend(ExecutionEventORM(**event.model_dump()) for event in events)
        rows.append(
            ExecutionRunProjectionORM(
                run_id=plan.run_id,
                stream_version=3,
                state_hash=canonical_state_hash(state),
                last_event_hash=events[-1].event_hash,
                terminal=True,
            )
        )
        rows.append(
            ExecutionRunViewORM(
                run_id=plan.run_id,
                scope_key=scope,
                projector_version=1,
                formal_position=events[-1].position,
                progress_position=0,
                observed_order=3,
                projection_revision=3,
                as_of=events[-1].occurred_at,
                completeness={},
            )
        )
        rows.extend(
            ExecutionViewObservationORM(
                run_id=plan.run_id,
                scope_key=scope,
                projector_version=1,
                formal_position=event.position,
                progress_position=0,
                observed_order=i + 1,
                projection_revision=i + 1,
                observed_at=event.occurred_at,
                source_kind="formal",
                source_identity=str(event.event_id),
                public_payload={"facts": [{"kind": "run", "id": run, "patch": public}]},
            )
            for i, event in enumerate(events)
        )
    now = datetime(2026, 9, 21, tzinfo=UTC)
    rows.append(
        UserORM(
            id=uid,
            email="fixture@example.test",
            username="fixture",
            password_hash=None,
            display_name="fixture",
            avatar_url="",
            global_role="user",
            status="active",
            token_version=3,
            created_at=now,
            updated_at=now,
            last_login_at=None,
        )
    )
    engine = create_engine("sqlite://")
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
    bid, dsid, cid, configid, judgeid, rubricid, suiteid, env_id, resultid, objectid = (
        UUID(int=100 + i) for i in range(10)
    )
    case = CaseRevision(id=cid, case_key="fixture-case", input="original private case")
    objectbody = json.dumps([case.model_dump(mode="json")]).encode()
    key = "private/case.json"
    contenthash = sha256(objectbody).hexdigest()
    member = {
        "id": cid,
        "revision": 1,
        "case_key": case.case_key,
        "object_id": objectid,
        "object_index": 0,
        "storage_key": key,
        "digest": contenthash,
        "cleaned_at": None,
    }
    configs_by_id = {
        identity: ConfigVersion(
            id=identity,
            entity_id=identity,
            revision=1,
            name="fixture",
            selection={"model_id": "fixture", "purpose": purpose, "mode": "ask"},
            fingerprint="fixture",
            snapshot={},
        )
        for identity, purpose in ((configid, "evaluation_subject"), (judgeid, "evaluation_judge"))
    }
    rubric = RubricVersion(
        id=rubricid,
        entity_id=rubricid,
        revision=1,
        name="fixture",
        fingerprint="fixture",
        judge_config_version=judgeid,
    )
    suite = SuiteVersion(
        id=suiteid,
        entity_id=suiteid,
        revision=1,
        name="fixture",
        quantity=1,
        fingerprint="fixture",
        dataset_version=dsid,
        config_versions=(configid,),
        rubric_version=rubricid,
        mode="isolated",
        environment_version=env_id,
        settings=SuiteSettings(token_budget=100),
        dataset_proof={
            "revision": 1,
            "membership_digest": "fixture",
            "resources": (),
            "reference_evidence": (),
        },
    )
    environment = EnvironmentVersion(
        id=env_id,
        image_digest={"kind": "local_content_id", "value": "sha256:" + "a" * 64},
        fixture_revision="fixture",
        reset_adapter="fixture",
        adapter_revision="fixture",
        healthcheck_revision="fixture",
    )
    versions = {**configs_by_id, rubricid: rubric, suiteid: suite}
    matrix = {
        "id": resultid,
        "ordinal": 0,
        "case_revision_id": cid,
        "config_version_id": configid,
        "repetition": 0,
        "run_id": subject.run_id,
        "execution_status": "succeeded",
        "scoring_status": "complete",
        "attempt": 0,
        "revision": 1,
    }
    attempt = {
        "result_id": resultid,
        "attempt": 0,
        "intent": {"committed": True},
        "case_revision_id": cid,
        "config_version_id": configid,
        "repetition": 0,
    }
    lease = EnvironmentLease(
        id=uuid5(subject.run_id, "environment"),
        environment_version=env_id,
        case_slot={
            "workspace": scope,
            "batch_id": bid,
            "case_id": cid,
            "config_version": configid,
            "repeat": 1,
        },
        generation=1,
        revision=1,
        state="verified_clean",
    )
    operation = {
        "id": UUID(int=200),
        "scope_key": scope,
        "lease_id": lease.id,
        "generation": 1,
        "claim_generation": 1,
        "phase": "cleanup",
        "lease_revision": 1,
        "status": "done",
        "error": None,
        "receipt": {"clean": True},
        "claim_until": None,
        "created_at": now,
    }
    request = {
        "lease": lease.model_dump(mode="json"),
        "version": environment.model_dump(mode="json"),
        "operation": {
            name: typed(operation[name])
            for name in ("id", "phase", "lease_revision", "claim_generation")
        },
    }
    request["operation"]["id"] = str(operation["id"])
    request_id = request_identity(request)
    sqlrows = {
        IDENTITY: [
            {
                "database_name": "fixture",
                "database_user": "fixture",
                "database_system_identifier": "42",
                "snapshot": "fixture:snapshot",
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
                "owner_scope_key": scope,
                "source_entity_type": "probe" if plan is probe else "batch",
                "source_entity_id": "probe" if plan is probe else str(bid),
                "correlation_id": "fixture",
                "stream_version": 3,
                "terminal": True,
            }
            for plan in (probe, subject)
        ],
        ATTEMPTS: [
            dict(**attempt, scope_key=scope, batch_id=bid, run_id=subject.run_id, current_attempt=0)
        ],
        JUDGES: [],
        PROJECTORS: [
            {
                "scope": scope,
                "head": 6,
                "checkpoint": 6,
                "generation": "live",
                "source_version": 1,
                "algorithm_version": 1,
            }
        ],
        "SELECT id,scope_key,suite_version,status FROM evaluation_batches ORDER BY scope_key,id": [
            {"id": bid, "scope_key": scope, "suite_version": suiteid, "status": "completed"}
        ],
        batch_facts.LEASE_SQL: [dict(**lease.model_dump(), scope_key=scope)],
        batch_facts.ATTEMPT_SQL: [{"run_id": subject.run_id, "attempt": 0}],
        batch_facts.OPERATION_SQL: [operation],
    }
    for table in cumulative_cleanup.TABLES:
        sqlrows["SELECT * FROM " + table] = []
    sqlrows["SELECT * FROM evaluation_batches"] = [
        {"id": bid, "scope_key": scope, "status": "completed", "cleanup_status": "clean"}
    ]
    sqlrows["SELECT * FROM evaluation_environment_leases"] = [
        dict(**lease.model_dump(), scope_key=scope)
    ]
    sqlrows["SELECT * FROM evaluation_environment_operations"] = [operation]
    sqlrows["SELECT * FROM evaluation_object_intents"] = [
        {
            "id": objectid,
            "scope_key": scope,
            "storage_key": key,
            "cleaned_at": None,
            "state": "retained",
        }
    ]
    sqlrows[
        "SELECT o.*,e.stream_id FROM execution_outbox o JOIN execution_events e ON e.position=o.event_position"
    ] = []
    for table, predicate in [
        ("execution_poisoned_runs", "TRUE"),
        ("execution_poisoned_scopes", "TRUE"),
        ("execution_recovery_requests", "status NOT IN ('completed')"),
        ("execution_view_generations", "status IN ('building','failed')"),
    ]:
        sqlrows[f"SELECT * FROM {table} WHERE {predicate}"] = []

    def query(sql, params):
        if sql in sqlrows:
            return sqlrows[sql]
        if sql.startswith("SELECT status,token_version"):
            return [{"status": "active", "token_version": 3, "global_role": "user"}]
        if sql.startswith("SELECT body FROM"):
            return [{"body": versions[params["id"]].model_dump(mode="json")}]
        if sql.startswith("SELECT id,dataset_id,revision"):
            return [{"id": dsid, "dataset_id": dsid, "revision": 1}]
        if sql.startswith("SELECT c.*"):
            return [member]
        if sql.startswith("SELECT revision,body,digest"):
            raw = environment.model_dump(mode="json")
            return [{"revision": 1, "body": raw, "digest": digest(raw)}]
        if sql.startswith("SELECT * FROM evaluation_batches WHERE"):
            return [
                {
                    "id": bid,
                    "revision": 1,
                    "status": "completed",
                    "review_status": "not_required",
                    "cleanup_status": "clean",
                }
            ]
        if sql.startswith("SELECT execution_status,count(*)"):
            return [{"execution_status": "succeeded", "count": 1}]
        if sql.startswith("SELECT r.*,a.run_id"):
            return [matrix] if params["after"] < 0 else []
        if sql.startswith("SELECT c.id AS case_id"):
            return [
                {
                    "case_id": cid,
                    "id": objectid,
                    "storage_key": key,
                    "digest": contenthash,
                    "cleaned_at": None,
                }
            ]
        if "SELECT a.result_id,a.attempt,a.intent" in sql:
            return [attempt]
        if "SELECT id,result_id,candidate" in sql:
            return []
        if sql.startswith("SELECT id,body,purpose"):
            return [configs[str(params["run"])]]
        if sql.startswith("SELECT active_generation"):
            return []
        if sql.startswith("SELECT f->>'id'"):
            return []
        if sql.startswith("SELECT count(*) AS count,"):
            import hashlib

            return [{"count": 3, "digest": hashlib.md5(b"1,2,3").hexdigest()}]
        if sql.startswith("SELECT completeness->"):
            return [{"value": []}]
        if sql.startswith(
            (
                "SELECT state_ref->",
                "SELECT * FROM execution_view_steps",
                "SELECT b.step_id",
                "SELECT * FROM execution_activity_tasks",
            )
        ):
            return []
        if sql.startswith("SELECT observed_order,projection_revision,source_kind"):
            return [
                {
                    "observed_order": i + 1,
                    "projection_revision": i + 1,
                    "source_kind": "formal",
                    "source_identity": str(event.event_id),
                    "projector_version": 1,
                }
                for i, event in enumerate(event_sets[str(params["run"])])
            ]
        return None

    original = Connection.execute

    def result(values):
        names = list(values[0]) if values else ["unused"]
        return IteratorResult(
            SimpleResultMetaData(names), iter(tuple(row[name] for name in names) for row in values)
        )

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement is _AUTHORIZATION_SQL:
            return result([{"value": "fixture"}])
        sql = str(statement)
        params = parameters or {}
        if statement.get_execution_options().get("c2c_preflight"):
            textsql = sql.split("FROM (", 1)[1].rsplit(") AS ", 1)[0]
            values = query(textsql, params)
            if values is None:
                # Actual ORM reads execute below on the private SQLite DB.
                count = (
                    3
                    if "FROM execution_events" in textsql
                    else 0
                    if "FROM execution_view_checkpoints" in textsql
                    else 1
                )
            else:
                count = len(values)
            return result(
                [
                    {
                        "row_count": count,
                        "max_bytes": 100 if count else 0,
                        "total_bytes": count * 100,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "fixture:snapshot",
                    }
                ]
            )
        if isinstance(statement, TextClause):
            values = query(statement.text, params)
            if values is None:
                raise AssertionError("unexpected fixture SQL " + statement.text)
            return result(values)
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=budget, evidence=owner, **kwargs)

    def sessions():
        return AsyncSession(sync_session_class=Bound)

    @asynccontextmanager
    async def snapshot(*args, budget=None):
        async with sessions() as session:
            yield InventoryQueries(session, budget=budget)

    for module in (inventory_reader, cumulative_cleanup, batch_facts):
        monkeypatch.setattr(module, "read_snapshot", snapshot)
    # These four precise fixed-population gates are the only semantic test
    # isolations. No SourceInventoryReader/result_inventory/validator is replaced.
    monkeypatch.setattr(inventory_reader, "standard_population", lambda *args: set())
    monkeypatch.setattr(inventory_reader, "probe_run_identity", lambda *args: str(probe.run_id))
    monkeypatch.setattr(inventory_reader.SourceInventoryReader, "_counts", lambda *args: None)
    monkeypatch.setattr(batch, "require_standard_matrix", lambda *args: None)
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    groups = {
        "ddl": [f"api/alembic/versions/{i}.py" for i in range(24)],
        "frontend": ["web.txt"],
        "dependencies": ["deps.txt"],
        "build": ["build.txt"],
    }
    for name in [n for values in groups.values() for n in values] + [
        "scripts/seed_execution_visualization.py",
        "scripts/execution_capacity/compose.yml",
        "scripts/execution_capacity/compose.live.yml",
        "e2e/fixtures/inference-provider/server.mjs",
    ]:
        path = source_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    build = read_build_inventory(source_root, groups, budget=budget)
    binding = {
        "environment": "test",
        "source_sha256": source_digest(source_root, budget=budget),
        "inventory_build_digest": build["digest"],
        "database_name": "fixture",
        "database_system_identifier": "42",
        "migration": "fixture",
        "fixture_id": str(UUID(int=1)),
        "principal_id": uid,
        "probe": {"fixture_id": str(UUID(int=2)), "principal_id": uid},
        "broker": {"container_id": "fixture-broker"},
    }

    class Objects:
        async def get_bounded_bytes(self, wanted, limit, *, observed=None):
            assert wanted == key
            if observed is not None:
                observed(0, objectbody)
            return SimpleNamespace(data=objectbody, truncated=False)

    objects = EvidenceObjects(Objects(), budget, object_limit=8192, evidence=owner)
    settings = SimpleNamespace(
        api_key_secret=cursor.decode(),
        api_key_secret_id="fixture",
        api_key_previous_secrets={},
        public_cursor_secret="",
        audit_signing_key="fixture",
        audit_signing_key_id="fixture",
        database_authorization_signing_secret=secret,
    )
    services = await version_services(
        SimpleNamespace(
            settings=settings,
            postgres=SimpleNamespace(session_factory=sessions),
            evidence_objects=objects,
        ),
        binding,
    )
    broker_path = tmp_path / "broker.sqlite"
    with sqlite3.connect(broker_path) as db:
        db.execute("CREATE TABLE operations(identity TEXT,fingerprint TEXT,result TEXT)")
        db.execute("CREATE TABLE bindings(identity TEXT,fingerprint TEXT)")
        db.execute(
            "INSERT INTO operations VALUES(?,?,?)",
            (request_id, digest(request), json.dumps(operation["receipt"])),
        )
        db.execute(
            "INSERT INTO bindings VALUES(?,?)",
            (
                str(lease.id) + ":1",
                digest({"slot": request["lease"]["case_slot"], "version": request["version"]}),
            ),
        )
    transport_calls = []

    def render(*args):
        transport_calls.append(args)
        if args[0] == "exec":
            return b"\n".join(json.dumps(row).encode() for row in sqlite_pages(broker_path)) + b"\n"
        assert args[0] in {"container", "network"}
        assert "ls" in args
        return b"retained-resource" if fault == "physical_present" else b""

    transport = bounded_inspect_fixture(budget, render, evidence=owner)
    private = tmp_path / "journal"
    private.mkdir(mode=0o700)
    writerroot = tmp_path / "writers"
    writerroot.mkdir(mode=0o700)
    from contextlib import ExitStack

    with (
        ExitStack() as base_lifetime,
        RecoveryJournal(
            private, budget=budget if owner.journal is not None else None, index_bytes=index_bytes
        ) as journal,
        RecoveryJournal(writerroot) as writerjournal,
    ):
        base_inventory = None if base_value is None else base_value.final["source"]
        if base_value is not None:
            source_owner = None
            if owner.journal is not None:
                from scripts.execution_capacity.inventory import SourceInventory

                actual_base = base_lifetime.enter_context(
                    round_owners[2].open_evidence(budget=budget)
                )
                owner.journal.bind_verified_base(actual_base)
                source_owner = actual_base.view.journal
                final_base = actual_base.roots["cleanup"]["quiescence"]["final"]
                inherited_source = owner.journal.reference_base_graph(final_base["source"])
                base_inventory = SourceInventory(**inherited_source)
            else:
                final_base = base_value.roots["cleanup"]["quiescence"]["final"]
            for family, records in {
                **final_base["retained_history"]["records"],
                **final_base["predicate_journals"],
            }.items():
                for identity, record in records.items():
                    journal.intent(
                        family,
                        identity,
                        record["body"],
                        body_owner=source_owner if family == "environment_read" else None,
                    )
                    if record["receipt"] is not None:
                        journal.acknowledge(family, identity, record["receipt"])
            for family, records in (
                ("writer", final_base["writers"]["writers"]),
                ("writer_supervisor", final_base["writers"]["supervisors"]),
                ("sdk_upload", final_base["writers"]["uploads"]),
            ):
                for identity, record in records.items():
                    writerjournal.intent(family, identity, record["body"])
                    if record["receipt"] is not None:
                        writerjournal.acknowledge(family, identity, record["receipt"])
        journal.intent("run", str(probe.run_id), {"scope": scope})
        journal.intent(
            "batch",
            str(bid),
            {
                "suite_id": str(suiteid),
                "dataset_id": str(dsid),
                "config_ids": [str(configid)],
                "judge_id": str(judgeid),
                "rubric_id": str(rubricid),
            },
        )
        journal.intent("broker_request", request_id, {"request": request})
        journal.acknowledge("broker_request", request_id, {"result": operation["receipt"]})
        writer_id = "fixture-writer" if base_value is None else "fixture-round-writer"
        if fault != "missing_writer":
            writerjournal.intent(
                "writer",
                writer_id,
                {"boot_id": "fixture-boot" if origin is None else origin.boot_id},
            )
            writerjournal.acknowledge("writer", writer_id, {"resource_closed": True})
        writerjournal.intent("writer_supervisor", writer_id, {"reports": []})
        port = "port" if base_value is None else "round-port"
        journal.intent("object", key, {"size": len(objectbody), "sha256": contenthash})
        journal.acknowledge("object", key, {"size": len(objectbody), "sha256": contenthash})
        if fault != "missing_port_ledger":
            journal.intent(
                "upload", port, {"key": key, "size": len(objectbody), "sha256": contenthash}
            )
            journal.acknowledge("upload", port, {"size": len(objectbody), "sha256": contenthash})
        writerjournal.intent(
            "sdk_upload",
            port,
            {
                "writer_id": writer_id,
                "port_upload_id": port,
                "key": key,
                "size": len(objectbody),
                "sha256": contenthash,
            },
        )
        writerjournal.acknowledge("sdk_upload", port, {"size": len(objectbody)})
        original_writer, before, after = inputs()
        current = deepcopy(before)

        def stop(*args):
            if args[0] == "stop":
                current.clear()
                current.update(deepcopy(after))
                return b""
            return json.dumps([current]).encode()

        writers = object.__new__(ContainerWriters)
        writers.root = writerroot
        writers.budget = budget
        writers.original = {"writer": original_writer}
        writers.exits = {}
        writers.exit_observations = []
        writers.writer_ids = {writer_id: "writer"}
        writers.base = (
            None
            if base_value is None
            else SimpleNamespace(
                **{
                    name: final_base["writers"][name]
                    for name in ("writers", "supervisors", "uploads")
                }
            )
        )
        writers.docker = stop
        writers.inspect_transport = bounded_inspect_fixture(budget, stop)
        writers.deployment = SimpleNamespace(binding={"provider_container": "writer"})

        async def fence():
            pass

        origin = origin or SourceOrigin(
            kind="base", seal_id="fixture-seal", round=None, clone_id=None, boot_id=None
        )
        cumulative = CumulativeJournal(
            (), journal, budget=budget, evidence=owner if owner.journal is not None else None
        )
        reader = inventory_reader.SourceInventoryReader(
            sessions=sessions,
            authorization=AuthorizationContext.system("execution-kernel"),
            journals=(cumulative,),
            binding=binding,
            seed=0,
            origin=origin,
            services=services,
            storage=objects,
            signing_secret=secret,
            source_root=source_root,
            build_groups=groups,
            host_fence=fence,
            evidence=owner,
        )
        storage = SimpleNamespace(
            client=SDK(
                [
                    b"<broken"
                    if fault == "incomplete_storage"
                    else object_page([(key, len(objectbody))]),
                    uploads(),
                ],
                {key: objectbody},
            ),
            bucket="owned",
        )
        final = FinalInventory(
            writers=writers,
            reader=reader,
            journal=cumulative,
            storage=storage,
            binding=binding,
            docker=transport,
            base=() if base_value is None else [base_inventory],
        )
        round_view = None
        if round_owners is None:
            quiescence = await cumulative_cleanup.quiesce(
                writers=writers, workloads=[], diagnostics=diagnostics, uploads=[], final=final
            )
        else:
            import time

            from scripts.execution_capacity.round_originals import OwnedRoundOriginals

            resources = SimpleNamespace(
                evidence=owner,
                evidence_objects=objects,
                evidence_transport=transport,
                closed_ns=None,
            )
            collector = OwnedRoundOriginals(*round_owners, resources=resources, final=final)
            quiescence = await collector.collect(workloads=[], diagnostics=diagnostics, uploads=[])
            resources.closed_ns = time.monotonic_ns()
            round_view = collector.finish()
        assert fault is not None or quiescence.complete, repr(
            (
                quiescence.errors,
                quiescence.final["issues"],
                quiescence.final["source"].errors,
                [r for r in owner.sql_reads if r.get("error")],
                budget.bytes,
                budget.rows,
            )
        )
    engine.dispose()
    raw = owner._copy(quiescence)
    roots = {
        "cleanup": {"quiescence": raw} if origin.kind == "base" else raw,
        "operands": owner.originals,
        "sql": owner.sql_reads,
        "objects": objects.originals,
        "transports": transport.originals,
    }
    return SimpleNamespace(
        roots=roots,
        owner=owner,
        origin=origin,
        budget=budget,
        secret=secret,
        cursor=cursor,
        final=quiescence.final,
        quiescence=quiescence,
        binding=binding,
        view=round_view,
        transport_calls=transport_calls,
    )


def seal_acquired(tmp_path, monkeypatch, value):
    """Actual b1 closeout/finalizer, private files; only VM effects are injected."""
    import base64
    import time
    from uuid import uuid4

    from scripts.execution_capacity import seal_finalizer as seal
    from scripts.execution_capacity.attempt import AttemptLedger, digest, encode
    from scripts.execution_capacity.c2c_export import close_originals, snapshot_cleanup
    from scripts.execution_capacity.guest_seal_entry import artifact_relative
    from scripts.execution_capacity.test_seal_finalizer import fixtures
    from scripts.execution_capacity.writer_base import export_writer_base

    uuid = str(uuid4())
    values, metadata = fixtures(uuid)
    metadata.update(
        seal_id=value.origin.seal_id,
        source_digest=value.binding["source_sha256"],
        migration="fixture",
    )
    typed_quiescence = value.roots["cleanup"]["quiescence"]
    from scripts.execution_capacity.evidence_json import chunks, json_digest

    raw = (
        typed_quiescence
        if value.owner.journal is not None
        else json.loads(b"".join(chunks(typed_quiescence, budget=value.budget)))
    )
    complete_digest = json_digest(raw, budget=value.budget, owner=value.owner.journal)
    source = value.final["source"]
    source_safe = source.safe(owner=value.owner.journal, budget=value.budget)
    payload = export_writer_base(
        value.quiescence, value.origin.seal_id, owner=value.owner.journal, budget=value.budget
    )
    payload["complete_quiescence_digest"] = complete_digest
    values["cleanup"].update(
        quiescence=raw,
        observer_closed_ns=time.monotonic_ns(),
        source_inventory=raw["final"]["source"],
        source_safe=source_safe,
        source_inventory_digest=source.safe_digest(owner=value.owner.journal, budget=value.budget),
        complete_quiescence_digest=complete_digest,
        writer_base=payload,
        writer_quiescence_digest=digest(payload),
        seal_metadata=metadata,
    )
    values["stop"]["stores_stopped_ns"] = time.monotonic_ns()
    values["offline"]["control"]["system_identifier"] = "42"
    value.roots["cleanup"] = (
        dict(values["cleanup"]) if value.owner.journal is not None else deepcopy(values["cleanup"])
    )
    value.roots["cleanup"]["quiescence"] = typed_quiescence
    value.roots["cleanup"]["source_inventory"] = typed_quiescence["final"]["source"]
    guest = tmp_path / "guest"
    guest.mkdir(mode=0o700, exist_ok=True)
    identity = {"source_digest": value.binding["source_sha256"]}
    config = {
        "seal_id": value.origin.seal_id,
        "protocol_id": "fixture-protocol",
        "identity": identity,
        "evidence_root": str(guest),
    }
    with AttemptLedger.create(
        guest / "operations",
        {"identity": identity, "config_digest": digest(config), "protocol_id": "fixture-protocol"},
    ) as ledger:
        ledger.evidence_owner = value.owner
        ledger.append("phase-intent", {"phase": "cleanup"})
        final = close_originals(guest, config, ledger, value.roots, budget=value.budget)
        if value.owner.journal is not None:
            value.owner.journal.close()
            from scripts.execution_capacity.cleanup_envelope import cleanup_envelope

            values["cleanup"] = cleanup_envelope(final, budget=value.budget)
        else:
            values["cleanup"]["c2c_final"] = final
        ledger.append("phase-complete", {"phase": "cleanup"})
        exported = snapshot_cleanup(guest, ledger, final, budget=value.budget)
    rule = {
        "config_digest": digest(config),
        "seal_id": value.origin.seal_id,
        "export": {"attempt_id": "fixture-parent", "protocol_id": "fixture-protocol", **metadata},
        "phase_timeout_seconds": 30,
    }
    with AttemptLedger.create(tmp_path / "base", {"seal": rule}) as ledger:
        vm = SimpleNamespace(
            ledger=ledger,
            plan=SimpleNamespace(uuid=uuid),
            identity={"pid": 42},
            process=SimpleNamespace(pid=42),
            pidfd=100,
        )

        def exit_vm(seconds):
            vm.pidfd = None
            ledger.append("qemu-exited", {"identity": vm.identity, "returncode": 0})
            return 0

        vm.wait_exit = exit_vm

        class Session:
            def __init__(self):
                self.ledger = ledger
                self.identity = identity
                self.agent = SimpleNamespace(shutdown=lambda: None)

            def seal_phase(self, phase, *, artifact=None, offset=None):
                if phase == "read":
                    data = (
                        (guest / artifact_relative(artifact)).read_bytes()
                        if artifact.startswith("c2c-")
                        else encode(values[artifact])
                    )
                    chunk = data[offset : offset + 128 * 1024]
                    return {
                        "artifact": artifact,
                        "offset": offset,
                        "size_bytes": len(data),
                        "data": base64.b64encode(chunk).decode(),
                        "eof": offset + len(chunk) == len(data),
                    }
                data = encode(values[phase])
                return {
                    "artifact": {"sha256": sha256(data).hexdigest(), "size_bytes": len(data)},
                    "c2c_export": exported if phase == "cleanup" else None,
                }

        def flatten(vm, output, **kwargs):
            from scripts.execution_capacity.seal_offline import hash_offline

            assert vm.pidfd is None
            output.write_bytes(b"fixture-private-image")
            output.chmod(0o600)
            return hash_offline(output)

        with monkeypatch.context() as patch:
            patch.setattr(seal, "verify_live_vm", lambda *args: None)
            patch.setattr(seal, "process_identity", lambda pid: {"pid": pid})
            patch.setattr(seal, "flatten", flatten)
            result = seal.finalize(
                vm,
                Session(),
                object(),
                tmp_path / "sealed.raw",
                budget=value.budget,
                index_bytes=None
                if value.owner.journal is None
                else value.owner.journal.index_bytes,
            )
    return result


async def acquire_round(tmp_path, monkeypatch, base, base_value, *, diagnostic=False):
    from uuid import uuid4

    from scripts.acceptance.capacity_models import SourceOrigin
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.offline_context import RoundLocation
    from scripts.execution_capacity.reference_round import reserve_round

    monkeypatch.setattr(
        "scripts.execution_capacity.attempt.host_clock",
        lambda: {
            "boot_id": "fixture-host",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )
    sample = {"sample_id": "fixture-sample", "physical_window_id": "fixture-window"}
    if diagnostic:
        from scripts.execution_capacity.test_retained_diagnostics import diagnostic_plan

        sample = diagnostic_plan().model_dump()
    parent_plan = {
        "attempt_id": "fixture-parent",
        "protocol_id": base.seal.protocol_id,
        "samples": [sample],
        "writer_base": base.private["writer_base"],
    }
    round_id, clone, boot = (str(uuid4()) for _ in range(3))
    with AttemptLedger.create(tmp_path / "parent", parent_plan) as parent:
        (parent.root / "rounds").mkdir(mode=0o700)
        child_root = parent.root / "rounds" / round_id
        intent = {
            "attempt_id": round_id,
            "sample_id": "fixture-sample",
            "window_id": "fixture-window",
            "nonce": str(uuid4()),
            "source_digest": base_value.binding["source_sha256"],
        }
        child_plan = {
            "protocol_id": base.seal.protocol_id,
            "samples": [sample],
            "writer_base": base.private["writer_base"],
            "round": {
                "parent_attempt_id": "fixture-parent",
                "round_id": round_id,
                "sample_id": "fixture-sample",
                "window_id": "fixture-window",
            },
            "vm": {
                "uuid": clone,
                "base": base.private["raw_path"],
                "base_identity": base.private["raw_identity"],
                "overlay": str(child_root / "root.qcow2"),
            },
            "guest_sessions": [intent],
        }
        binding = reserve_round(
            parent, child_plan, child_root, seal_digest=base.private["raw_identity"]["sha256"]
        )
        with AttemptLedger.create(child_root, child_plan) as child:
            child.bind_clock()
            child.reserve(
                "fixture-sample",
                "fixture-window",
                seal_digest=base.private["raw_identity"]["sha256"],
            )
            child.append(
                "overlay-create-intent",
                {
                    "uuid": clone,
                    "base": child_plan["vm"]["base"],
                    "overlay": child_plan["vm"]["overlay"],
                },
            )
            child.append(
                "overlay-created",
                {
                    "uuid": clone,
                    "identity": {"device": 1, "inode": 2},
                    "chain": [
                        {
                            "format": "qcow2",
                            "full-backing-filename": child_plan["vm"]["base"],
                            "virtual-size": 100,
                        },
                        {
                            "format": "raw",
                            "filename": child_plan["vm"]["base"],
                            "virtual-size": 100,
                        },
                    ],
                },
            )
            child.append("guest-discovery-intent", {"identity": intent})
            child.append("guest-discovered", {"identity": {**intent, "boot_id": boot}})
            origin = SourceOrigin(
                kind="round",
                seal_id=base.seal.seal_id,
                round=binding.safe(),
                clone_id=clone,
                boot_id=boot,
            )
            fixture_root = tmp_path / "round-fixture"
            fixture_root.mkdir(mode=0o700)
            diagnostics = []
            if diagnostic:
                from scripts.execution_capacity.test_retained_diagnostics import diagnostic_job

                diagnostics = [
                    diagnostic_job(
                        fixture_root, monkeypatch, parent, child, binding, origin, base_value
                    )
                ]
            value = await acquire_final(
                fixture_root,
                monkeypatch,
                origin=origin,
                base_value=base_value,
                round_owners=(parent, child, base),
                budget=base_value.budget,
                diagnostics=diagnostics,
                original_root=child_root / "c2c-originals"
                if base.index_bytes is not None
                else None,
                index_bytes=base.index_bytes,
            )
            location = RoundLocation(
                parent.root, parent.root, child.root, child.root, base.ledger_root
            )
            # Acquisition has returned its durable export. The next consumer
            # reopens it through OfflineProofContext with an independent base.
            value.view.close()
            if value.owner.journal is not None:
                value.owner.journal.close()
    return value, location
