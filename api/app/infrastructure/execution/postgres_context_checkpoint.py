"""Publish only formal compact completions; stale writers cannot replace a head."""

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.application.execution import activity_types
from app.domain.errors import ConflictError
from app.domain.execution.context_checkpoint import CheckpointSummary, ContextCheckpoint
from app.domain.execution.context_models import ContextCompilation
from app.domain.execution.context_summary import ContextSummary, ContextSummaryProposal
from app.infrastructure.execution.context_checkpoint_models import checkpoints, heads
from app.infrastructure.execution.context_formal_request import context_activity_request
from app.infrastructure.execution.context_summary_models import proposals
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


class PostgresContextCheckpointStore:
    def __init__(self, session, *, objects, sources):
        self.session, self.objects, self.sources = session, objects, sources

    async def publish_completed(
        self, work, scope, *, run_id, prepare_activity_id, request_id, expected_revision
    ):
        if work.db_session is not self.session:
            raise ValueError("CONTEXT_CHECKPOINT_TRANSACTION_REQUIRED")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("CONTEXT_CHECKPOINT_REVISION_INVALID")
        key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
        # A request may not be reused for another collection or prepare operation.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": key + ":checkpoint-request:" + str(request_id)},
        )
        prior = (
            (
                await self.session.execute(
                    select(checkpoints).where(
                        checkpoints.c.scope_key == key, checkpoints.c.request_id == request_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            saved = ContextCheckpoint.model_validate(prior["body"])
            if (
                saved.digest != prior["digest"]
                or saved.run_id != run_id
                or saved.prepare_activity_id != prepare_activity_id
                or prior["expected_revision"] != expected_revision
            ):
                raise ConflictError("CONTEXT_CHECKPOINT_REQUEST_CONFLICT")
            return saved, expected_revision + 1
        append_lock = PostgresEventStore._scope_advisory_lock_key(
            None if scope.team_id else scope.user_id, scope.team_id
        )
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": append_lock})
        from app.infrastructure.execution.execution_object_lifecycle import guard_object_reference

        await guard_object_reference(self.session, key, run_id)
        loaded = await PostgresVerifiedRunLoader(self.session).load(
            str(run_id), owner_scope=(None if scope.team_id else scope.user_id, scope.team_id)
        )
        state = loaded.replay.state
        if state.definition_pin is None or state.definition_manifest is None:
            raise ValueError("CONTEXT_CHECKPOINT_DEFINITION_UNAVAILABLE")
        result = next(
            (item for item in state.activity_results if item[0] == prepare_activity_id), None
        )
        if (
            result is None
            or not any(
                identity == prepare_activity_id
                and kind == activity_types.CONTEXT_PREPARE
                and generation == result[1]
                for identity, kind, generation in state.requested_activities
            )
            or (prepare_activity_id, "succeeded", result[1]) not in state.settled_activities
        ):
            raise ValueError("CONTEXT_CHECKPOINT_PREPARATION_INCOMPLETE")
        prepared = await self.objects.load_context_document(result[2])
        plan = ContextCompilation.model_validate(prepared.get("compilation")).compaction
        if (
            prepared.get("kind") != "context_prepare"
            or plan is None
            or plan.definition_pin != state.definition_pin
            or plan.policy != state.definition_manifest.context_policy
            or plan.boundary.scope_key != key
        ):
            raise ValueError("CONTEXT_CHECKPOINT_PREPARATION_MISMATCH")
        rows = (
            (
                await self.session.execute(
                    select(proposals.c.body).where(
                        proposals.c.scope_key == key, proposals.c.run_id == run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        values = [ContextSummaryProposal.model_validate(body) for body in rows]
        summary_values, closure = [], {}
        for chunk in plan.chunks:
            candidates = [
                value
                for value in values
                if value.chunk_digest == chunk.digest
                and value.ordinal == chunk.ordinal
                and value.request_generation == result[1]
            ]
            if len(candidates) != 1:
                raise ValueError("CONTEXT_CHECKPOINT_COMPACT_INCOMPLETE")
            proposal = candidates[0]
            formal = next(
                (
                    item
                    for item in state.activity_results
                    if item[0] == proposal.activity_id and item[1] == proposal.request_generation
                ),
                None,
            )
            request_body = await context_activity_request(
                self.session,
                state,
                proposal.activity_id,
                proposal.request_generation,
                activity_types.CONTEXT_COMPACT,
            )
            if (
                proposal.definition_pin != state.definition_pin
                or formal is None
                or formal[2] != proposal.result_ref
                or (proposal.activity_id, "succeeded", proposal.request_generation)
                not in state.settled_activities
                or not isinstance(request_body, dict)
                or request_body.get("prepare_ref") != result[2]
                or request_body.get("prepare_activity_id") != str(prepare_activity_id)
                or request_body.get("compaction_digest") != plan.digest
            ):
                raise ValueError("CONTEXT_CHECKPOINT_COMPACT_INCOMPLETE")
            body = await self.objects.load_result_bounded(proposal.result_ref)
            if body.get("kind") != "context_compact":
                raise ValueError("CONTEXT_CHECKPOINT_RESULT_MISMATCH")
            summary = ContextSummary.model_validate(body.get("summary"))
            summary.require_chunk(chunk)
            summary_values.append(
                CheckpointSummary(
                    activity_id=proposal.activity_id,
                    result_ref=proposal.result_ref,
                    result_digest=proposal.result_digest,
                    chunk_digest=chunk.digest,
                    original_closure=chunk.sources,
                )
            )
            for ref in chunk.sources:
                previous = closure.setdefault(ref.source_id, ref)
                if previous != ref:
                    raise ValueError("CONTEXT_CHECKPOINT_SOURCE_MISMATCH")
        value = ContextCheckpoint(
            run_id=run_id,
            prepare_activity_id=prepare_activity_id,
            definition_pin=state.definition_pin,
            boundary=plan.boundary,
            compaction_digest=plan.digest,
            summaries=tuple(summary_values),
            original_closure=tuple(
                sorted(closure.values(), key=lambda item: (item.formal_position, item.source_id))
            ),
        )
        await self.sources.validate_in_uow(work, value.boundary, value.original_closure)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": key + ":checkpoint-head:" + value.boundary.collection_id},
        )
        head = (
            (
                await self.session.execute(
                    select(heads)
                    .where(
                        heads.c.scope_key == key,
                        heads.c.collection_id == value.boundary.collection_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if (head is None and expected_revision != 0) or (
            head is not None
            and (
                head["revision"] != expected_revision
                or head["through_position"] > value.boundary.through_position
            )
        ):
            raise ConflictError("CONTEXT_CHECKPOINT_HEAD_CHANGED")
        await self.session.execute(
            insert(checkpoints).values(
                scope_key=key,
                request_id=request_id,
                collection_id=value.boundary.collection_id,
                expected_revision=expected_revision,
                digest=value.digest,
                body=value.model_dump(mode="json"),
            )
        )
        body = {
            "request_id": request_id,
            "revision": expected_revision + 1,
            "through_position": value.boundary.through_position,
        }
        if head is None:
            await self.session.execute(
                insert(heads).values(
                    scope_key=key, collection_id=value.boundary.collection_id, **body
                )
            )
        else:
            await self.session.execute(
                update(heads)
                .where(
                    heads.c.scope_key == key,
                    heads.c.collection_id == value.boundary.collection_id,
                    heads.c.revision == expected_revision,
                )
                .values(**body)
            )
        return value, expected_revision + 1

    async def select(self, work, boundary, *, compiler_version, authorization_epoch=None):
        if work.db_session is not self.session:
            raise ValueError("CONTEXT_CHECKPOINT_TRANSACTION_REQUIRED")
        query = select(checkpoints.c.body, checkpoints.c.digest)
        if boundary.checkpoint_revision is not None:
            # Select the admitted revision, even if a newer writer has since
            # advanced the live head. Immutable copies make the cut repeatable.
            query = (
                query.where(
                    checkpoints.c.scope_key == boundary.scope_key,
                    checkpoints.c.collection_id == boundary.collection_id,
                    checkpoints.c.expected_revision < boundary.checkpoint_revision,
                )
                .order_by(checkpoints.c.expected_revision.desc())
                .limit(1)
            )
        else:
            query = query.join(
                heads,
                (heads.c.scope_key == checkpoints.c.scope_key)
                & (heads.c.request_id == checkpoints.c.request_id),
            ).where(
                heads.c.scope_key == boundary.scope_key,
                heads.c.collection_id == boundary.collection_id,
                heads.c.through_position <= boundary.through_position,
            )
        row = (await self.session.execute(query)).mappings().one_or_none()
        if row is None:
            return None
        value = ContextCheckpoint.model_validate(row["body"])
        if (
            value.digest != row["digest"]
            or value.boundary.scope_key != boundary.scope_key
            or value.boundary.collection_id != boundary.collection_id
        ):
            raise ValueError("CONTEXT_CHECKPOINT_INTEGRITY_INVALID")
        if value.definition_pin.compiler_version != compiler_version:
            return None
        if value.boundary.through_position > boundary.through_position:
            return None
        # Authority belongs to the consumer, not the original checkpoint writer.
        closure = value.original_closure
        if authorization_epoch is not None:
            closure = tuple(
                ref.model_copy(update={"authorization_epoch": authorization_epoch})
                for ref in closure
            )
        await self.sources.validate_in_uow(work, boundary, closure)
        for item in value.summaries:
            body = await self.objects.load_result_bounded(item.result_ref)
            summary = ContextSummary.model_validate(body.get("summary"))
            if body.get("kind") != "context_compact" or summary.chunk_digest != item.chunk_digest:
                raise ValueError("CONTEXT_CHECKPOINT_RESULT_MISMATCH")
            if any(
                source_id not in {ref.source_id for ref in item.original_closure}
                for fact in summary.facts
                for source_id in fact.source_ids
            ):
                raise ValueError("CONTEXT_CHECKPOINT_SOURCE_MISMATCH")
        return value
