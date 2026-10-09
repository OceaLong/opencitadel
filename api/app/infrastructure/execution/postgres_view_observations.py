"""Transactional, per-run serialized source journal for execution views."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Boolean, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.application.execution.view_facts import (
    ProjectionFact,
    attempt_key,
    request_key,
    safe_text,
    sanitize_patch,
    to_view_facts,
)
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.models.execution_view import (
    ExecutionRunViewORM,
    ExecutionStepViewORM,
    ExecutionViewObservationORM,
)

PROJECTOR_VERSION = 1
_UNKNOWN = {
    "state": "partial",
    "missing_fields": ["parent_step_id", "invocation_id"],
    "missing_intervals": [],
}


async def observe(
    session,
    *,
    run_id,
    owner_user_id,
    team_id,
    family,
    fact,
    source_identity,
    event_id,
    occurred_at,
    facts=None,
    source=None,
    parent_activity_id=None,
    production_event=None,
    usage_event=None,
    content_event=None,
):
    """Return the immutable source observation, reusing its original cut on replay.

    Caller owns the transaction. Acquire this lock before other mutable run
    projections. Counter allocation, source dedupe and projection are atomic.
    """
    from app.infrastructure.execution.postgres_execution_view import writer_barrier

    await writer_barrier(session, f"team:{team_id}" if team_id else f"user:{owner_user_id}")
    await session.execute(
        pg_insert(ExecutionRunViewORM)
        .values(
            run_id=run_id,
            owner_user_id=owner_user_id,
            team_id=team_id,
            created_by="execution-projector",
            family=family,
            status="unknown",
            purpose="unknown",
            completeness={
                "state": "partial",
                "missing_fields": ["configuration", "purpose"],
                "missing_intervals": [
                    {"start": None, "end": None, "reason": "pre_journal_progress_unavailable"}
                ],
            },
            capabilities=[],
            projection_revision=0,
            projector_version=PROJECTOR_VERSION,
            formal_position=0,
            progress_position=0,
            observed_order=0,
        )
        .on_conflict_do_nothing(index_elements=["run_id"])
    )
    # Run identity and owner/scope keys never change here. NO KEY UPDATE
    # serializes all counter writers while allowing scoped cache FK KEY SHARE.
    run = await session.scalar(
        select(ExecutionRunViewORM)
        .where(ExecutionRunViewORM.run_id == run_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if run.owner_user_id != owner_user_id or run.team_id != team_id:
        raise ValueError("observation scope mismatch")
    existing = await session.scalar(
        select(ExecutionViewObservationORM).where(
            ExecutionViewObservationORM.run_id == run_id,
            ExecutionViewObservationORM.source_kind == fact.source_kind,
            ExecutionViewObservationORM.source_identity == source_identity,
            ExecutionViewObservationORM.projector_version == PROJECTOR_VERSION,
        )
    )
    if existing is not None:
        existing.new_source = False
        return existing
    facts = list(facts) if facts is not None else [fact]
    applicable = True
    if source is not None:
        prior = await session.scalar(
            select(ExecutionViewObservationORM.public_payload["source"])
            .where(
                ExecutionViewObservationORM.run_id == run_id,
                ExecutionViewObservationORM.source_kind == "progress",
                ExecutionViewObservationORM.projector_version == PROJECTOR_VERSION,
                ExecutionViewObservationORM.public_payload["source"]["activity_id"].astext
                == source["activity_id"],
                func.coalesce(
                    cast(ExecutionViewObservationORM.public_payload["applied"].astext, Boolean),
                    True,
                ),
            )
            .order_by(ExecutionViewObservationORM.observed_order.desc())
            .limit(1)
        )

        def local_order(item):
            return (item["generation"], item["claim_generation"], item["sequence"])

        applicable = prior is None or local_order(source) > local_order(prior)
        if not applicable:
            facts = []
    # Resolve metadata only under the Run lock. Never guess which claim ended.
    enriched = []
    for item in facts:
        patch = dict(item.patch)
        if item.kind == "step" and item.source_kind == "formal":
            request = await session.scalar(
                select(ExecutionStepViewORM).where(
                    ExecutionStepViewORM.run_id == run_id,
                    ExecutionStepViewORM.step_id == request_key(patch["activity_id"]),
                )
            )
            if request is None:
                request = await session.scalar(
                    select(ExecutionStepViewORM)
                    .where(
                        ExecutionStepViewORM.run_id == run_id,
                        ExecutionStepViewORM.activity_id == UUID(patch["activity_id"]),
                    )
                    .order_by(ExecutionStepViewORM.observed_order, ExecutionStepViewORM.step_id)
                    .limit(1)
                )
            # Lifecycle facts do not revoke request linkage. After replacing a
            # placeholder the metadata source may be this very attempt; the
            # legacy unknown start also shares its request ID. Enrich both
            # cases before writing the immutable journal and live row.
            if request is not None:
                for key in ("kind", "tool_name", "invocation_id", "parent_step_id", "relationship"):
                    value = getattr(request, key)
                    if value is not None:
                        patch[key] = str(value) if isinstance(value, UUID) else value
            if (
                request is not None
                and request.step_id == request_key(patch["activity_id"])
                and item.entity_id != request.step_id
                and request.status == "queued"
                and request.started_at is None
            ):
                enriched.append(
                    replace(
                        item,
                        entity_id=request.step_id,
                        patch={
                            "removed": True,
                            "replacement_step_id": item.entity_id,
                            "logical_step_id": patch["logical_step_id"],
                        },
                    )
                )
            if parent_activity_id:
                parents = (
                    await session.scalars(
                        select(ExecutionStepViewORM).where(
                            ExecutionStepViewORM.run_id == run_id,
                            ExecutionStepViewORM.activity_id == UUID(str(parent_activity_id)),
                            ExecutionStepViewORM.status == "completed",
                        )
                    )
                ).all()
                if len(parents) == 1:
                    patch.update(parent_step_id=parents[0].step_id, relationship="direct")
            previous = await session.scalar(
                select(ExecutionStepViewORM).where(
                    ExecutionStepViewORM.run_id == run_id,
                    ExecutionStepViewORM.step_id == item.entity_id,
                )
            )
            started_at = patch.get("started_at") or (
                previous.started_at.isoformat()
                if previous is not None and previous.started_at
                else None
            )
            ended_at = patch.get("ended_at")
            if started_at and ended_at:
                patch["duration_ms"] = max(
                    0,
                    int(
                        (
                            datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
                        ).total_seconds()
                        * 1000
                    ),
                )
            patch["completeness"] = {
                "state": "partial",
                "missing_fields": [
                    key
                    for key in ("parent_step_id", "invocation_id", "attempt_id", "started_at")
                    if not (started_at if key == "started_at" else patch.get(key))
                ],
                "missing_intervals": [],
            }
        enriched.append(replace(item, patch=sanitize_patch(item.kind, patch)))
    facts = enriched
    run.observed_order += 1
    run.projection_revision += 1
    if fact.source_kind == "formal":
        run.formal_position = max(run.formal_position, fact.formal_position)
    else:
        run.progress_position += 1
    observed_at = datetime.now(UTC)
    run.as_of = run.latest_available = observed_at
    run.updated_at = observed_at
    row = ExecutionViewObservationORM(
        run_id=run_id,
        observed_order=run.observed_order,
        source_kind=fact.source_kind,
        source_identity=source_identity,
        event_id=event_id,
        formal_position=run.formal_position,
        progress_position=run.progress_position,
        observed_at=observed_at,
        occurred_at=occurred_at,
        projection_revision=run.projection_revision,
        projector_version=PROJECTOR_VERSION,
        public_payload={
            "facts": [item.playback_patch() for item in facts],
            **({"source": source, "applied": applicable} if source is not None else {}),
        },
        owner_user_id=owner_user_id,
        team_id=team_id,
        created_by="execution-projector",
    )
    session.add(row)
    for item in facts:
        if item.source_kind == "formal" and item.kind == "run":
            for key in (
                "status",
                "wait_reason",
                "family",
                "admitted_at",
                "terminal_at",
                "source",
                "purpose",
                "configuration",
                "execution_mode",
                "completeness",
            ):
                if key in item.patch:
                    value = item.patch[key]
                    if key.endswith("_at") and value is not None:
                        value = datetime.fromisoformat(value)
                    setattr(run, key, value)
                    if key == "configuration":
                        run.configuration_revision = (value or {}).get("configuration_revision")
                        run.model_revision = (value or {}).get("model_revision")
        elif item.source_kind == "formal" and item.kind == "step":
            if item.patch.get("removed"):
                await session.execute(
                    delete(ExecutionStepViewORM).where(
                        ExecutionStepViewORM.run_id == run_id,
                        ExecutionStepViewORM.step_id == item.entity_id,
                    )
                )
                continue
            step = await session.scalar(
                select(ExecutionStepViewORM).where(
                    ExecutionStepViewORM.run_id == run_id,
                    ExecutionStepViewORM.step_id == item.entity_id,
                )
            )
            if step is None:
                step = ExecutionStepViewORM(
                    id=uuid5(NAMESPACE_URL, f"opencitadel:view-step:{run_id}:{item.entity_id}"),
                    run_id=run_id,
                    step_id=item.entity_id,
                    kind="activity",
                    status="unknown",
                    relationship="unknown",
                    completeness=_UNKNOWN,
                    projection_revision=run.projection_revision,
                    observed_order=run.observed_order,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    created_by="execution-projector",
                )
                session.add(step)
            for key, value in item.patch.items():
                if key.endswith("_at") and value is not None:
                    value = datetime.fromisoformat(value)
                if key in ("activity_id", "invocation_id") and value is not None:
                    value = UUID(value)
                setattr(step, key, value)
            step.projection_revision = run.projection_revision
            step.observed_order = run.observed_order
    await session.flush()
    if production_event is not None:
        from app.infrastructure.execution.postgres_artifact_provenance import bind_production

        await bind_production(session, event=production_event, observation=row)
    if usage_event is not None:
        from app.infrastructure.execution.postgres_execution_usage import project_usage

        await project_usage(session, event=usage_event, observation=row)
    if content_event is not None:
        from app.infrastructure.execution.postgres_execution_content import project_content

        await project_content(session, event=content_event, observation=row)
    from app.infrastructure.execution.postgres_playback import maybe_write_checkpoint

    await maybe_write_checkpoint(session, run=run, observation=row)
    from app.infrastructure.execution.postgres_execution_view import refresh_active_shadow

    await refresh_active_shadow(session, run, row)
    row.new_source = True
    return row


async def observe_formal(session, event, state):
    facts = list(to_view_facts(event))
    # Approval and retry facts also change the Run; record the complete public
    # lifecycle patch in the same immutable source bundle.
    run_fact = ProjectionFact(
        event.position,
        0,
        None,
        "run",
        event.stream_id,
        {"status": state.status.value, "wait_reason": safe_text(state.wait_reason, 128)},
        "formal",
    )
    if facts and facts[0].kind == "run":
        facts[0] = replace(facts[0], patch={**facts[0].patch, **run_fact.patch})
    else:
        facts.insert(0, run_fact)
    return await observe(
        session,
        run_id=UUID(event.stream_id),
        owner_user_id=event.owner_user_id,
        team_id=event.team_id,
        family=state.family.value if state.family else "unknown",
        fact=facts[0],
        facts=facts,
        source_identity=str(event.event_id),
        event_id=event.event_id,
        occurred_at=event.occurred_at,
        parent_activity_id=event.public_payload.get("parent_activity_id"),
        production_event=event if event.event_type == "ArtifactVersionProduced" else None,
        usage_event=event if event.event_type == "ModelUsageRecorded" else None,
        content_event=event
        if event.event_type in ("ActivityCallStarted", "ActivityCompleted")
        else None,
    )


async def observe_progress(session, record):
    # Immutable source metadata only; no operational activity locks are taken.
    created = await session.scalar(
        select(ExecutionEventORM)
        .where(
            ExecutionEventORM.stream_type == "run",
            ExecutionEventORM.stream_id == str(record.run_id),
            ExecutionEventORM.event_type == "RunCreated",
            ExecutionEventORM.owner_user_id == record.owner_user_id,
            ExecutionEventORM.team_id == record.team_id,
        )
        .limit(1)
    )
    if created is None:
        raise ValueError("progress source run unavailable")
    from app.domain.execution.run import RunAggregate
    from app.infrastructure.execution.postgres_event_store import PostgresEventStore

    creation = PostgresEventStore._to_stored(created)
    PostgresEventStore._verify_position_read((creation,))
    aggregate = RunAggregate()
    state = aggregate.evolve(aggregate.initial_state(str(record.run_id)), creation)
    # Reuses the same formal source identity when the normal projector catches
    # up; no invented status and no second ordering authority.
    await observe_formal(session, creation, state)
    source = {
        "activity_id": str(record.activity_id),
        "generation": record.generation,
        "claim_generation": record.claim_generation,
        "sequence": record.sequence,
    }
    fact = ProjectionFact(
        0,
        0,
        None,
        record.kind,
        attempt_key(str(record.activity_id), record.generation, record.claim_generation),
        {
            "progress": record.progress,
            "phase": safe_text(record.phase, 64),
            "progress_status": record.status,
            "public_summary": safe_text(record.message),
        },
        "progress",
    )
    return await observe(
        session,
        run_id=record.run_id,
        owner_user_id=record.owner_user_id,
        team_id=record.team_id,
        family=created.public_payload["family"],
        fact=fact,
        source=source,
        source_identity=str(record.event_id),
        event_id=None,
        occurred_at=record.occurred_at,
    )
