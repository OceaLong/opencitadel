"""Receipt-authorized production metadata and crash-safe object cleanup."""

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text

from app.application.execution.view_facts import ProjectionFact, attempt_key
from app.domain.execution.commands import CommandEnvelope
from app.infrastructure.models.execution_view import (
    ArtifactVersionProvenanceORM,
    ExecutionStepViewORM,
)
from app.infrastructure.security.db_authorization import configure_session_authorization

logger = logging.getLogger(__name__)


def receipt_payload(row):
    return {
        key: str(row[key]) if isinstance(row[key], UUID) else row[key]
        for key in (
            "operation_id",
            "artifact_id",
            "version",
            "activity_id",
            "generation",
            "claim_generation",
            "invocation_id",
        )
    }


async def authorize_production(session, command):
    """Lock immutable receipt through append+event marker commit; no inbox TTL reliance."""
    row = (
        (
            await session.execute(
                text("""SELECT r.* FROM artifact_production_receipts r
        JOIN artifacts a ON a.id=r.artifact_id
        JOIN sessions s ON s.id=a.session_id
        JOIN artifact_version_provenance p ON p.id=r.association_id
        WHERE r.operation_id=:operation AND r.run_id=:run
          AND r.owner_user_id IS NOT DISTINCT FROM :owner AND r.team_id IS NOT DISTINCT FROM :team
          AND ((r.team_id IS NOT NULL AND s.team_id=r.team_id) OR (r.team_id IS NULL AND s.team_id IS NULL AND s.owner_user_id=r.owner_user_id))
          AND a.version_refs->>(r.version-1)=r.storage_key AND p.content_digest=r.content_digest
          AND p.artifact_id=r.artifact_id AND p.version=r.version
        FOR UPDATE OF r"""),
                {
                    "operation": UUID(command.payload["operation_id"]),
                    "run": UUID(command.stream_id),
                    "owner": command.owner_user_id,
                    "team": command.team_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or receipt_payload(row) != dict(command.payload):
        return None
    return row


async def bind_production(session, *, event, observation):
    """One exact event binds one committed receipt before the cut is frozen."""
    payload = event.internal_payload
    receipt = (
        (
            await session.execute(
                text("""SELECT * FROM artifact_production_receipts
        WHERE operation_id=:operation AND run_id=:run AND event_id=:event AND event_position=:position
          AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team
        FOR UPDATE"""),
                {
                    "operation": UUID(payload["operation_id"]),
                    "run": UUID(event.stream_id),
                    "event": event.event_id,
                    "position": event.position,
                    "owner": event.owner_user_id,
                    "team": event.team_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if receipt is None or receipt_payload(receipt) != dict(payload):
        raise ValueError("unverified artifact production event")
    step_id = attempt_key(
        str(receipt["activity_id"]), receipt["generation"], receipt["claim_generation"]
    )
    step = await session.scalar(
        select(ExecutionStepViewORM).where(
            ExecutionStepViewORM.run_id == receipt["run_id"],
            ExecutionStepViewORM.step_id == step_id,
            ExecutionStepViewORM.owner_user_id.is_not_distinct_from(event.owner_user_id),
            ExecutionStepViewORM.team_id.is_not_distinct_from(event.team_id),
        )
    )
    if step is None or (
        receipt["invocation_id"] is not None and step.invocation_id != receipt["invocation_id"]
    ):
        raise ValueError("exact producer attempt authority unavailable")
    row = await session.get(ArtifactVersionProvenanceORM, receipt["association_id"])
    if row.binding_status == "pending":
        row.producer_run_id = receipt["run_id"]
        row.activity_id = receipt["activity_id"]
        row.attempt_id = step.attempt_id
        row.invocation_id = step.invocation_id
        row.producer_step_ids = [step.step_id]
        row.produced_event_id = event.event_id
        row.boundary = observation.formal_position
        row.revision = observation.projection_revision
        row.binding_status = "bound"
        row.updated_at = datetime.now(UTC)
    elif (
        row.binding_status != "bound"
        or row.produced_event_id != event.event_id
        or row.producer_run_id != receipt["run_id"]
        or row.activity_id != receipt["activity_id"]
        or row.attempt_id != step.attempt_id
        or row.invocation_id != step.invocation_id
        or row.producer_step_ids != [step.step_id]
        or row.boundary != event.position
    ):
        raise ValueError("artifact production already bound to different authority")
    patch = ProjectionFact(
        event.position,
        0,
        None,
        "artifact",
        f"{row.artifact_id}:{row.version}",
        {"artifact_id": row.artifact_id, "version": row.version, "availability": row.availability},
        "formal",
    ).playback_patch()
    refs = list(step.artifact_refs or [])
    reference = {
        "artifact_id": row.artifact_id,
        "version": row.version,
        "availability": row.availability,
    }
    if reference not in refs:
        refs.append(reference)
    step.artifact_refs = refs
    step.projection_revision = observation.projection_revision
    step_patch = ProjectionFact(
        event.position, 0, None, "step", step.step_id, {"artifact_refs": refs}, "formal"
    ).playback_patch()
    observation.public_payload = {
        **observation.public_payload,
        "facts": [*observation.public_payload["facts"], patch, step_patch],
    }
    await session.execute(
        text(
            "UPDATE artifact_production_receipts SET bound_at=coalesce(bound_at,CURRENT_TIMESTAMP),reconciliation_status='bound',updated_at=CURRENT_TIMESTAMP WHERE operation_id=:operation"
        ),
        {"operation": receipt["operation_id"]},
    )
    await session.flush()


class ArtifactProvenanceMaintenance:
    def __init__(self, *, session_factory, authorization, objects, handler):
        self._sessions = session_factory
        self._authorization = authorization
        self._objects = objects
        self._handler = handler
        self._object_delete_timeout = 10
        self._cleanup_authority_timeout = 30

    async def process_pending(self, *, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError("invalid artifact maintenance limit")
        result = {"emitted": 0, "unavailable": 0, "deferred": 0, "cleaned": 0}
        try:
            async with self._sessions() as session:
                await configure_session_authorization(session, self._authorization)
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM artifact_production_receipts WHERE event_id IS NULL AND reconciliation_status='pending' AND next_attempt_at<=CURRENT_TIMESTAMP ORDER BY next_attempt_at,created_at,operation_id LIMIT :limit"
                            ),
                            {"limit": limit},
                        )
                    )
                    .mappings()
                    .all()
                )
            for row in rows:
                command = CommandEnvelope(
                    command_id=row["operation_id"],
                    command_type="RecordArtifactVersionProduced",
                    command_schema_version=2,
                    stream_type="run",
                    stream_id=str(row["run_id"]),
                    owner_user_id=row["owner_user_id"],
                    team_id=row["team_id"],
                    correlation_id=row["run_id"],
                    causation_id=None,
                    issued_at=row["created_at"],
                    payload=receipt_payload(row),
                )
                try:
                    async with asyncio.timeout(10):
                        if await self._settle_unavailable(command):
                            result["unavailable"] += 1
                            continue
                        delivered = await self._handler.handle(command)
                        if delivered.status == "accepted":
                            result["emitted"] += 1
                        elif delivered.status == "rejected" and await self._settle_unavailable(
                            command
                        ):
                            result["unavailable"] += 1
                        else:
                            await self._defer(row, "command_" + delivered.status)
                            result["deferred"] += 1
                except Exception:  # noqa: BLE001 - isolate each durable receipt
                    # Each immutable receipt remains durable. Persist bounded
                    # retry scheduling without leaking SQL/payload exceptions.
                    logger.warning("artifact receipt deferred operation=%s", row["operation_id"])
                    try:
                        # Recovery can contend on the same receipt as the failed
                        # probe. Its own budget must expire before moving on.
                        async with asyncio.timeout(1):
                            await self._defer(row, "transient_failure")
                    except Exception:  # noqa: BLE001 - isolate each durable receipt
                        logger.warning(
                            "artifact retry scheduling unavailable operation=%s",
                            row["operation_id"],
                        )
                    result["deferred"] += 1
        finally:
            original_failure = sys.exc_info()[0] is not None
            try:
                result["cleaned"] = await self.cleanup_uploads(limit=limit)
            except Exception:
                if not original_failure:
                    raise
                logger.warning(
                    "artifact cleanup failed while preserving original maintenance failure"
                )
        return result

    async def _settle_unavailable(self, command):
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            receipt = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM artifact_production_receipts WHERE operation_id=:id FOR UPDATE"
                        ),
                        {"id": command.command_id},
                    )
                )
                .mappings()
                .one()
            )
            if receipt["event_id"] is not None:
                return False
            if receipt["reconciliation_status"] == "unavailable":
                return True
            if await authorize_production(session, command) is not None:
                return False
            exists = await session.scalar(
                text("SELECT EXISTS(SELECT 1 FROM artifacts WHERE id=:id)"),
                {"id": receipt["artifact_id"]},
            )
            reason = "invalid_authority" if exists else "artifact_unavailable"
            await session.execute(
                text(
                    "UPDATE artifact_version_provenance SET binding_status='unavailable',availability='unavailable',updated_at=CURRENT_TIMESTAMP WHERE id=:id AND binding_status='pending'"
                ),
                {"id": receipt["association_id"]},
            )
            await session.execute(
                text(
                    "UPDATE artifact_production_receipts SET reconciliation_status='unavailable',last_error=:reason,settled_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE operation_id=:id AND event_id IS NULL"
                ),
                {"id": command.command_id, "reason": reason},
            )
            await session.commit()
            return True

    async def _defer(self, row, reason):
        delay = min(3600, 5 * 2 ** min(row["retry_attempts"], 10))
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            await session.execute(
                text(
                    "UPDATE artifact_production_receipts SET retry_attempts=retry_attempts+1,next_attempt_at=:next,last_error=:reason,updated_at=CURRENT_TIMESTAMP WHERE operation_id=:id AND event_id IS NULL AND reconciliation_status='pending'"
                ),
                {
                    "id": row["operation_id"],
                    "next": datetime.now(UTC) + timedelta(seconds=delay),
                    "reason": reason,
                },
            )
            await session.commit()

    async def cleanup_uploads(self, *, limit=100, before=None):
        if not 1 <= limit <= 1000:
            raise ValueError("invalid artifact cleanup limit")
        cleaned = 0
        # Separate failure/transaction budgets: neither durable authority is
        # chained behind successful processing of the other queue.
        for cleanup in (self._cleanup_upload_intents, self._cleanup_retired):
            try:
                async with asyncio.timeout(self._cleanup_authority_timeout):
                    if cleanup == self._cleanup_upload_intents:
                        cleaned += await cleanup(limit=limit, before=before)
                    else:
                        cleaned += await cleanup(limit=limit)
            except Exception:  # noqa: BLE001 - keep independent cleanup authority live
                logger.warning("artifact cleanup authority deferred kind=%s", cleanup.__name__)
        return cleaned

    async def _cleanup_upload_intents(self, *, limit, before=None):
        before = before or datetime.now(UTC) - timedelta(hours=1)
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM artifact_upload_intents WHERE cleaned_at IS NULL AND created_at<:before ORDER BY updated_at,created_at,upload_id LIMIT :limit FOR UPDATE SKIP LOCKED"
                        ),
                        {"before": before, "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            cleaned = 0
            for candidate in rows:
                # The previous attempt committed and cleared SET LOCAL claims.
                # Production sessionmakers do not rebind them on autobegin.
                await configure_session_authorization(session, self._authorization)
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM artifact_upload_intents WHERE upload_id=:id AND cleaned_at IS NULL FOR UPDATE SKIP LOCKED"
                            ),
                            {"id": candidate["upload_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    continue
                try:
                    await session.execute(
                        text(
                            "UPDATE artifact_upload_intents SET updated_at=CURRENT_TIMESTAMP WHERE upload_id=:id"
                        ),
                        {"id": row["upload_id"]},
                    )
                    locked = await session.scalar(
                        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                        {"key": "artifact-upload:" + str(row["upload_id"])},
                    )
                    if not locked:
                        continue
                    # Fresh READ COMMITTED lookup after acquiring writer's lock.
                    referenced = await session.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM artifacts WHERE id=:id AND session_id=:session AND version_refs @> CAST(:ref AS jsonb))"
                        ),
                        {
                            "id": row["artifact_id"],
                            "session": row["session_id"],
                            "ref": __import__("json").dumps([row["storage_key"]]),
                        },
                    )
                    if not referenced:
                        try:
                            async with asyncio.timeout(self._object_delete_timeout):
                                await self._objects.delete_bytes(row["storage_key"])
                        except Exception:  # noqa: BLE001 - one unavailable object must not starve the batch
                            logger.warning("upload cleanup deferred upload=%s", row["upload_id"])
                            continue
                        cleaned += 1
                    await session.execute(
                        text(
                            "UPDATE artifact_upload_intents SET cleaned_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE upload_id=:id AND cleaned_at IS NULL"
                        ),
                        {"id": row["upload_id"]},
                    )
                finally:
                    # Each attempt's rotation/settlement survives later queue
                    # failure or the enclosing authority timeout.
                    async with asyncio.timeout(1):
                        await session.commit()
            await session.commit()
            return cleaned

    async def _cleanup_retired(self, *, limit):
        """Deletion-trigger authority; F05 successful upload receipts remain settled."""
        async with self._sessions() as session:
            await configure_session_authorization(session, self._authorization)
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM artifact_retired_objects WHERE cleaned_at IS NULL ORDER BY updated_at,created_at,retirement_id LIMIT :limit FOR UPDATE SKIP LOCKED"
                        ),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            cleaned = 0
            for candidate in rows:
                # The previous attempt committed and cleared SET LOCAL claims.
                # Production sessionmakers do not rebind them on autobegin.
                await configure_session_authorization(session, self._authorization)
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM artifact_retired_objects WHERE retirement_id=:id AND cleaned_at IS NULL FOR UPDATE SKIP LOCKED"
                            ),
                            {"id": candidate["retirement_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    continue
                try:
                    # Rotate every attempted item, including contention/failure, so a
                    # full batch of unavailable objects cannot starve later retirees.
                    await session.execute(
                        text(
                            "UPDATE artifact_retired_objects SET updated_at=CURRENT_TIMESTAMP WHERE retirement_id=:id"
                        ),
                        {"id": row["retirement_id"]},
                    )
                    # Same order as F05 writers; try-lock avoids maintenance convoy.
                    if not await session.scalar(
                        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                        {"key": "artifact-version:" + row["artifact_id"]},
                    ):
                        continue
                    upload = await session.scalar(
                        text(
                            "SELECT upload_id FROM artifact_upload_intents WHERE storage_key=:key AND artifact_id=:id AND session_id=:session AND scope_key=:scope"
                        ),
                        {
                            "key": row["storage_key"],
                            "id": row["artifact_id"],
                            "session": row["session_id"],
                            "scope": row["scope_key"],
                        },
                    )
                    if upload is not None and not await session.scalar(
                        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                        {"key": "artifact-upload:" + str(upload)},
                    ):
                        continue
                    referenced = await session.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM artifacts WHERE version_refs @> CAST(:ref AS jsonb)) OR EXISTS(SELECT 1 FROM resource_pins WHERE resource_kind='artifact' AND resource_id=:id AND available)"
                        ),
                        {
                            "ref": __import__("json").dumps([row["storage_key"]]),
                            "id": row["artifact_id"],
                        },
                    )
                    if referenced:
                        continue
                    try:
                        async with asyncio.timeout(self._object_delete_timeout):
                            await self._objects.delete_bytes(row["storage_key"])
                    except (OSError, RuntimeError, ValueError, TimeoutError):
                        logger.warning(
                            "retired artifact cleanup deferred retirement=%s", row["retirement_id"]
                        )
                        continue
                    await session.execute(
                        text(
                            "UPDATE artifact_retired_objects SET cleaned_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE retirement_id=:id AND cleaned_at IS NULL"
                        ),
                        {"id": row["retirement_id"]},
                    )
                    cleaned += 1
                finally:
                    # Each attempt's rotation/settlement survives later queue
                    # failure or the enclosing authority timeout.
                    async with asyncio.timeout(1):
                        await session.commit()
            await session.commit()
            return cleaned
