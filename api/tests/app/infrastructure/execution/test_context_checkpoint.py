# ruff: noqa: F401,F811 -- isolated real Worker fixture dependencies
"""Checkpoint CAS consumes formal compact completions, with live source checks."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.execution.decisions.base import activity_identity
from app.domain.errors import ConflictError
from app.domain.execution.context_summary import ContextCompactBinding, summary_activity_key
from app.infrastructure.execution.postgres_context_checkpoint import PostgresContextCheckpointStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def finish_chunks(runtime):
    assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
    for chunk in runtime.plan.chunks[1:]:
        async with runtime.uow(runtime.auth) as work:
            state = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    str(runtime.binding.run_id), owner_scope=(runtime.scope.user_id, None)
                )
            ).replay.state
        binding = ContextCompactBinding(
            prepare_activity_id=runtime.prepare_id,
            prepare_ref=runtime.prepared_ref,
            compaction_digest=runtime.plan.digest,
            chunk_digest=chunk.digest,
            round_index=0,
            ordinal=chunk.ordinal,
        )
        await runtime.command(
            "RequestActivity",
            {
                "activity_id": str(activity_identity(state, summary_activity_key(binding))),
                "activity_type": "context.compact",
                "input_ref": runtime.input_ref,
                "input_digest": runtime.input_digest,
                "input_payload": binding.model_dump(mode="json"),
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
            4,
        )
        assert (await runtime.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1


def store(work, runtime):
    return PostgresContextCheckpointStore(
        work.db_session, objects=runtime.objects, sources=runtime.source_validator
    )


async def publish(work, runtime, request_id, revision):
    return await store(work, runtime).publish_completed(
        work,
        runtime.scope,
        run_id=runtime.binding.run_id,
        prepare_activity_id=runtime.prepare_id,
        request_id=request_id,
        expected_revision=revision,
    )


async def test_b04_incomplete_compact_cannot_publish_and_stale_checkpoint_cas_is_rejected(
    compact_runtime,
):
    runtime = compact_runtime
    async with runtime.uow(runtime.auth) as work:
        with pytest.raises(ValueError, match="CONTEXT_CHECKPOINT_COMPACT_INCOMPLETE"):
            await publish(work, runtime, uuid4(), 0)
    await finish_chunks(runtime)
    request_id = uuid4()
    async with runtime.uow(runtime.auth) as work:
        checkpoint, revision = await publish(work, runtime, request_id, 0)
        assert revision == 1
        assert len(checkpoint.summaries) == len(runtime.plan.chunks)
        await work.commit()
    sends = len(runtime.sends)
    async with runtime.uow(runtime.auth) as work:
        assert await publish(work, runtime, request_id, 0) == (checkpoint, 1)
        await work.commit()
    assert len(runtime.sends) == sends
    async with runtime.uow(runtime.auth) as work:
        with pytest.raises(ConflictError, match="CONTEXT_CHECKPOINT_HEAD_CHANGED"):
            await publish(work, runtime, uuid4(), 0)
    async with runtime.uow(runtime.auth) as work:
        selected = await store(work, runtime).select(
            work, runtime.plan.boundary, compiler_version=checkpoint.definition_pin.compiler_version
        )
        assert selected == checkpoint
        assert (
            await store(work, runtime).select(
                work,
                runtime.plan.boundary.model_copy(update={"through_position": 0}),
                compiler_version=checkpoint.definition_pin.compiler_version,
            )
            is None
        )
        assert (
            await store(work, runtime).select(
                work, runtime.plan.boundary, compiler_version="unsupported-compiler"
            )
            is None
        )


async def test_b05_revoked_originals_make_saved_checkpoint_unavailable(compact_runtime):
    runtime = compact_runtime
    await finish_chunks(runtime)
    async with runtime.uow(runtime.auth) as work:
        checkpoint, _ = await publish(work, runtime, uuid4(), 0)
        await work.commit()
    runtime.checks.revoke_in_uow = True
    async with runtime.uow(runtime.auth) as work:
        with pytest.raises(ValueError, match="CONTEXT_SOURCE_AUTHORIZATION_REVOKED"):
            await store(work, runtime).select(
                work,
                runtime.plan.boundary,
                compiler_version=checkpoint.definition_pin.compiler_version,
            )


async def test_b06_guessed_other_scope_checkpoint_does_not_read_private_objects(compact_runtime):
    runtime = compact_runtime
    await finish_chunks(runtime)
    async with runtime.uow(runtime.auth) as work:
        checkpoint, _ = await publish(work, runtime, uuid4(), 0)
        await work.commit()
    reads = len(runtime.objects._storage.reads)
    async with runtime.uow(runtime.auth) as work:
        other = runtime.plan.boundary.model_copy(update={"scope_key": "user:someone-else"})
        assert (
            await store(work, runtime).select(
                work, other, compiler_version=checkpoint.definition_pin.compiler_version
            )
            is None
        )
    assert len(runtime.objects._storage.reads) == reads


async def test_accepted_cut_keeps_original_checkpoint_revision_after_head_advances(compact_runtime):
    from app.infrastructure.execution.postgres_context_sources import PostgresContextSources

    runtime = compact_runtime
    request = uuid4()
    async with runtime.uow(runtime.auth) as work:
        cuts = PostgresContextSources(work.db_session, signing_secret="owned-checkpoint-cut")
        before = await cuts.freeze(
            runtime.scope, runtime.plan.boundary.collection_id, request_id=request
        )
        assert before.checkpoint_revision == 0
        await work.commit()
    await finish_chunks(runtime)
    async with runtime.uow(runtime.auth) as work:
        await publish(work, runtime, uuid4(), before.checkpoint_revision)
        await work.commit()
    async with runtime.uow(runtime.auth) as work:
        cuts = PostgresContextSources(work.db_session, signing_secret="owned-checkpoint-cut")
        assert (
            await cuts.freeze(
                runtime.scope, runtime.plan.boundary.collection_id, request_id=request
            )
            == before
        )
        after = await cuts.freeze(
            runtime.scope, runtime.plan.boundary.collection_id, request_id=uuid4()
        )
        assert after.checkpoint_revision == 1
        assert (
            await store(work, runtime).select(
                work,
                runtime.plan.boundary.model_copy(
                    update={"checkpoint_revision": before.checkpoint_revision}
                ),
                compiler_version=runtime.definitions.manifests[0].compiler_version,
            )
            is None
        )
        await work.commit()
