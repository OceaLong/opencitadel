"""Prepare-time publication uses the admitted revision, never the latest head."""

from uuid import uuid5

from sqlalchemy import select

from app.application.execution import activity_types
from app.domain.errors import ConflictError
from app.domain.execution.context_models import ContextCompilation
from app.infrastructure.execution.context_checkpoint_models import checkpoints
from app.infrastructure.execution.postgres_context_checkpoint import PostgresContextCheckpointStore


async def publish_completed_checkpoints(work, state, boundary, *, scope, objects, sources):
    store = PostgresContextCheckpointStore(work.db_session, objects=objects, sources=sources)
    published = (
        await work.db_session.execute(
            select(
                checkpoints.c.request_id,
                checkpoints.c.expected_revision,
            ).where(
                checkpoints.c.scope_key == boundary.scope_key,
                checkpoints.c.collection_id == boundary.collection_id,
                checkpoints.c.body["run_id"].astext == str(state.run_id),
            )
        )
    ).all()
    done = {identity for identity, _ in published}
    revision = max((value + 1 for _, value in published), default=boundary.checkpoint_revision or 0)
    for identity, generation, ref, _, _ in state.activity_results:
        if (
            generation != state.retry_generation
            or (identity, activity_types.CONTEXT_PREPARE, generation)
            not in state.requested_activities
        ):
            continue
        request_id = uuid5(state.run_id, f"context-checkpoint:{identity}")
        if request_id in done:
            continue
        compilation = ContextCompilation.model_validate(
            (await objects.load_context_document(ref)).get("compilation")
        )
        if compilation.compaction is None:
            continue
        try:
            # A failed optional publication must not roll back useful preparation.
            async with work.db_session.begin_nested():
                _, revision = await store.publish_completed(
                    work,
                    scope,
                    run_id=state.run_id,
                    prepare_activity_id=identity,
                    request_id=request_id,
                    expected_revision=revision,
                )
        except ConflictError:
            # A different Run won the CAS. This Run keeps its own formal summaries;
            # it cannot adopt the new head revision and overwrite that winner.
            break
        except ValueError as error:
            if str(error) != "CONTEXT_CHECKPOINT_COMPACT_INCOMPLETE":
                raise
