# ruff: noqa: F401,F811 -- isolated PostgreSQL fixture graph
"""Retention fences use non-bypass roles and real formal streams."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.execution.events import NewEvent
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.external.object_storage import BoundedObjectBytes, ObjectNotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.execution_object_lifecycle import (
    ExecutionObjectGC,
    ExecutionObjectLifecycle,
    RetainedExecutionStorage,
    guard_object_reference,
)
from app.infrastructure.execution.execution_object_roots import has_execution_obligation
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import run_policy_snapshot_json
from tests.app.infrastructure.execution.test_definition_lifecycle import (
    datasets,
    definition_runtime,
    fresh_f07_database,
    isolated_database,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]
AUTH = AuthorizationContext.system("execution-kernel")


class Objects:
    def __init__(self):
        self.data, self.deletes = {}, []
        self.fail_put = self.fail_delete = False
        self.entered = self.resume = None

    async def put_bytes(self, key, body):
        if self.entered is not None:
            self.entered.set()
            await self.resume.wait()
        if self.fail_put:
            raise OSError("owned upload interruption")
        self.data[key] = body

    async def get_bytes(self, key):
        if key not in self.data:
            raise ObjectNotFoundError(key)
        return self.data[key]

    async def get_bounded_bytes(self, key, limit):
        body = await self.get_bytes(key)
        return BoundedObjectBytes(body[:limit], len(body) > limit)

    async def delete_bytes(self, key):
        self.deletes.append(key)
        if self.fail_delete:
            raise OSError("owned DELETE interruption")
        self.data.pop(key, None)


def setup(runtime, *, batch_size=100):
    _, scope, _, sessions = runtime
    raw = Objects()
    secret = load_deployment_settings().database_authorization_signing_secret
    life = ExecutionObjectLifecycle(sessions, raw, authorization=AUTH, signing_secret=secret)
    gc = ExecutionObjectGC(sessions, raw, signing_secret=secret, batch_size=batch_size)
    return scope, sessions, raw, life, gc


def identity(body=b"private original"):
    run = uuid4()
    return run, f"execution/inputs/{run}/{hashlib.sha256(body).hexdigest()}.json", body


async def state(sessions, run):
    async with sessions() as db:
        await configure_session_authorization(db, AUTH)
        return dict(
            (
                await db.execute(
                    text("SELECT * FROM execution_object_runs WHERE run_id=:run"), {"run": run}
                )
            )
            .mappings()
            .one()
        )


async def formal(sessions, scope, run, key, *, collection=None, terminal=True, parent=None):
    events = [
        NewEvent(
            event_type="RunCreated",
            event_schema_version=3,
            public_payload={
                "family": "ask",
                "source_entity_type": "session" if collection else "owned-gc-test",
                "source_entity_id": collection or str(run),
                "parent_run_id": str(parent) if parent else None,
                "input": {},
            },
            internal_payload={
                "semantic_payload": {"input_ref": key, "input_digest": key.rsplit("/", 1)[-1][:-5]},
                "policy_snapshot": run_policy_snapshot_json("ask"),
                "definition_pin": None,
                "definition_manifest": None,
            },
        ),
        NewEvent(
            event_type="RunStarted", event_schema_version=1, public_payload={}, internal_payload={}
        ),
    ]
    if terminal:
        events.append(
            NewEvent(
                event_type="RunCompleted",
                event_schema_version=1,
                public_payload={"result_ref": key},
                internal_payload={},
            )
        )
    async with sessions() as db:
        await configure_session_authorization(db, AUTH)
        await PostgresEventStore(db).append(
            StreamRef(stream_type="run", stream_id=str(run)),
            0,
            tuple(events),
            AppendContext(
                owner_user_id=scope.user_id,
                team_id=None,
                correlation_id=run,
                causation_id=uuid4(),
                occurred_at=datetime.now(UTC),
            ),
        )
        await db.commit()


async def test_unpublished_upload_failure_and_late_put_cannot_revive_tombstone(definition_runtime):
    scope, sessions, raw, life, gc = setup(definition_runtime)
    run, key, body = identity()
    raw.fail_put = True
    with pytest.raises(OSError, match="upload interruption"):
        await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
    assert (await state(sessions, run))["state"] == "live"
    later = datetime.now(UTC) + timedelta(days=2)
    assert (await gc.cleanup(now=later))["deleted_objects"] == 1
    assert (await state(sessions, run))["state"] == "deleted"
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_UNAVAILABLE"):
        await life.put(key, body, run_id=run)
    raw.fail_put = False
    run, key, body = identity(b"late upload")
    raw.entered, raw.resume = asyncio.Event(), asyncio.Event()
    pending = asyncio.create_task(
        life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
    )
    await asyncio.wait_for(raw.entered.wait(), 10)
    try:
        assert (await gc.cleanup(now=later))["deleted_objects"] == 1
    finally:
        raw.resume.set()
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_UNAVAILABLE"):
        await pending
    assert key in raw.data  # Lost worker's late PUT really landed.
    with pytest.raises(ObjectNotFoundError):
        await RetainedExecutionStorage(life).get_bytes(key)
    assert (await gc.cleanup(now=later + timedelta(hours=2)))["deleted_objects"] == 2
    assert key not in raw.data
    async with sessions() as db:
        await configure_session_authorization(db, AUTH)
        with pytest.raises(DBAPIError, match="object_tombstone_immutable"):
            await db.execute(
                text(
                    "UPDATE execution_object_runs SET state='live',deleting_at=NULL WHERE run_id=:run"
                ),
                {"run": run},
            )


async def test_delete_failure_retries_and_batch_limit_rotates_candidates(definition_runtime):
    scope, sessions, raw, life, gc = setup(definition_runtime, batch_size=1)
    ids = [identity(str(i).encode()) for i in range(3)]
    for run, key, body in ids:
        await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
    later = datetime.now(UTC) + timedelta(days=2)
    raw.fail_delete = True
    failed = await gc.cleanup(now=later)
    assert failed == {"marked_runs": 1, "deleted_objects": 0, "errors": 1}
    raw.fail_delete = False
    for i in range(3):
        result = await gc.cleanup(now=later + timedelta(minutes=i + 2))
        assert result["deleted_objects"] == 1
        assert result["errors"] == 0
    assert not raw.data
    assert all([(await state(sessions, run))["state"] == "deleted" for run, _, _ in ids])


async def test_formal_obligations_and_terminal_retention_override_age(definition_runtime):
    scope, sessions, raw, life, gc = setup(definition_runtime)
    active, ended = identity(b"active"), identity(b"ended")
    for (run, key, body), terminal in [(active, False), (ended, True)]:
        await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
        await formal(sessions, scope, run, key, terminal=terminal)
    assert (await gc.cleanup(now=datetime.now(UTC) + timedelta(days=2)))["marked_runs"] == 0
    assert (await state(sessions, active[0]))["reason"] == "execution_obligation"
    assert (await state(sessions, ended[0]))["reason"] == "terminal_retention"
    result = await gc.cleanup(now=datetime.now(UTC) + timedelta(days=32))
    assert result == {"marked_runs": 1, "deleted_objects": 1, "errors": 0}
    assert active[1] in raw.data
    assert ended[1] not in raw.data
    async with sessions() as db:
        await configure_session_authorization(db, AUTH)
        with pytest.raises(ValueError, match="CONTEXT_SOURCE_UNAVAILABLE"):
            await guard_object_reference(db, f"user:{scope.user_id}", ended[0])


async def test_soft_deleted_session_and_collection_dependency_are_retained(definition_runtime):
    service, _, principal, _ = definition_runtime
    scope, sessions, raw, life, gc = setup(definition_runtime)
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    async with service.uow_factory(auth) as work:
        collection = await work.db_session.scalar(text("SELECT id FROM sessions LIMIT 1"))
        await work.db_session.execute(
            text("UPDATE sessions SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": collection},
        )
        await work.commit()
    first, second = identity(b"old"), identity(b"recent")
    for run, key, body in (first, second):
        await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
        await formal(sessions, scope, run, key, collection=collection)
    assert (await gc.cleanup(now=datetime.now(UTC) + timedelta(days=90)))["marked_runs"] == 0
    assert (await state(sessions, first[0]))["reason"] == "retained_session"
    # Even after the session is physically removed, an active descendant retains originals.
    async with service.uow_factory(auth) as work:
        await work.db_session.execute(text("DELETE FROM sessions WHERE id=:id"), {"id": collection})
        await work.commit()
    active = identity(b"active descendant")
    await life.put(active[1], active[2], run_id=active[0], scope_key=f"user:{scope.user_id}")
    await formal(sessions, scope, active[0], active[1], collection=collection, terminal=False)
    assert (await gc.cleanup(now=datetime.now(UTC) + timedelta(days=90)))["marked_runs"] == 0
    assert len(raw.data) == 3


async def test_api_role_only_narrow_owned_uploads_and_publication_lock(definition_runtime):
    service, scope, principal, _sessions = definition_runtime
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    _, _, raw, _, gc = setup(definition_runtime)
    factory = service.uow_factory(auth).session_factory
    life = ExecutionObjectLifecycle(
        factory,
        raw,
        authorization=auth,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    run, key, body = identity()
    with pytest.raises(DBAPIError, match="object_scope_denied"):
        await life.put(key, body, run_id=run, scope_key="user:another-owner")
    await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
    async with service.uow_factory(auth) as work:
        with pytest.raises(DBAPIError, match="permission denied"):
            await work.db_session.execute(text("UPDATE execution_object_runs SET state='deleted'"))
    async with service.uow_factory(auth) as work:
        await guard_object_reference(work.db_session, f"user:{scope.user_id}", run)
        # GC skips the locked scope rather than racing accepted publication.
        assert (await gc.cleanup(now=datetime.now(UTC) + timedelta(days=2)))["marked_runs"] == 0
    assert (await gc.cleanup(now=datetime.now(UTC) + timedelta(days=2)))["marked_runs"] == 1


async def test_cancelled_abandoned_unknown_and_pending_results_still_have_obligations():
    state = SimpleNamespace(
        status="cancelled", active_activity_ids=(), pending_approval_id=None, effect_operations=()
    )
    assert not has_execution_obligation(state)
    for effect_state, awaiting, conflict in [
        ("unknown", False, False),
        ("abandoned", False, False),
        ("applied", True, False),
        ("applied", False, True),
    ]:
        state.effect_operations = (
            SimpleNamespace(
                state=effect_state, awaiting_result=awaiting, integrity_conflict=conflict
            ),
        )
        assert has_execution_obligation(state)


async def test_recording_and_public_content_pin_retain_entire_original_collection(
    definition_runtime,
):
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.evaluation.recording import RecordingJob
    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    service, _, principal, _ = definition_runtime
    scope, sessions, raw, life, gc = setup(definition_runtime)
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    first, summary = identity(b"first original"), identity(b"derived summary")
    collection = str(uuid4())  # Already purged session; only evidence roots remain.
    for run, key, body in (first, summary):
        await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
        await formal(sessions, scope, run, key, collection=collection)
    content, command, activity = uuid4(), uuid4(), uuid4()
    digest = hashlib.sha256(b"summary").hexdigest()
    async with sessions() as db:
        await configure_session_authorization(db, AUTH)
        await db.execute(
            text("""
INSERT INTO execution_public_content(content_id,command_id,run_id,activity_id,generation,claim_generation,phase,body,content_digest,byte_length,redacted,owner_user_id,created_by)
VALUES(:id,:command,:run,:activity,0,1,'output','summary',:digest,7,false,:owner,:owner)
"""),
            {
                "id": content,
                "command": command,
                "run": summary[0],
                "activity": activity,
                "digest": digest,
                "owner": scope.user_id,
            },
        )
        await db.execute(
            text("""
INSERT INTO execution_content_bindings(event_id,phase,content_id,run_id,step_id,formal_position,owner_user_id,created_by)
VALUES(:command,'output',:id,:run,'summary',3,:owner,:owner)
"""),
            {"command": command, "id": content, "run": summary[0], "owner": scope.user_id},
        )
        await db.commit()
    job = RecordingJob(id=uuid4(), source_run_id=summary[0])
    async with service.uow_factory(auth) as work:
        await work.evaluation_recording.create(scope, job, [], principal)
        await work.commit()
    later = datetime.now(UTC) + timedelta(days=90)
    assert await gc.cleanup(now=later) == {"marked_runs": 0, "deleted_objects": 0, "errors": 0}
    assert (await state(sessions, first[0]))["reason"] == "retained_evidence"
    resource = ResourceIdentity(
        resource_kind="execution_content", resource_id=str(content), resource_version=digest
    )
    async with service.uow_factory(auth) as work:
        owner = await work.db_session.scalar(text("SELECT id FROM sessions LIMIT 1"))
        pins = DBResourcePinRepository(work.db_session)
        await pins.acquire(scope, "session", owner, [resource])
        # The recording fails after a pin was published; the pin independently retains originals.
        await work.db_session.execute(
            text("UPDATE evaluation_recording_jobs SET status='failed' WHERE id=:id"),
            {"id": job.id},
        )
        await work.commit()
    assert (await gc.cleanup(now=later))["marked_runs"] == 0
    async with service.uow_factory(auth) as work:
        await DBResourcePinRepository(work.db_session).release(scope, "session", owner, [resource])
        await work.commit()
    assert await gc.cleanup(now=later) == {"marked_runs": 2, "deleted_objects": 2, "errors": 0}
    assert not raw.data
    async with service.uow_factory(auth) as work:
        from app.infrastructure.repositories.db_execution_content_repository import (
            DBExecutionContentRepository,
        )

        assert (
            await DBExecutionContentRepository(work.db_session).get_snapshot(
                scope, str(content), summary[0], "summary", 3, include_body=False
            )
            is None
        )
    async with service.uow_factory(auth) as work:
        with pytest.raises(ResourceUnavailable, match="original execution content unavailable"):
            await DBResourcePinRepository(work.db_session).acquire(
                scope, "session", owner, [resource]
            )
    async with service.uow_factory(auth) as work:
        with pytest.raises(DatasetNotFound, match="recording_unavailable"):
            await work.evaluation_recording.create(
                scope, RecordingJob(id=uuid4(), source_run_id=summary[0]), [], principal
            )


async def test_child_original_retained_by_active_parent_in_another_collection(definition_runtime):
    scope, sessions, raw, life, gc = setup(definition_runtime)
    parent, child = identity(b"parent"), identity(b"child")
    for run, key, body in (parent, child):
        await life.put(key, body, run_id=run, scope_key=f"user:{scope.user_id}")
    await formal(sessions, scope, parent[0], parent[1], terminal=False)
    await formal(sessions, scope, child[0], child[1], parent=parent[0])
    assert await gc.cleanup(now=datetime.now(UTC) + timedelta(days=90)) == {
        "marked_runs": 0,
        "deleted_objects": 0,
        "errors": 0,
    }
    assert len(raw.data) == 2
    assert (await state(sessions, child[0]))["reason"] == "execution_obligation"
