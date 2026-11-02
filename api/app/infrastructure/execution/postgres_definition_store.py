"""Definition publication is a CAS transaction, separate from executable code."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.errors import ConflictError
from app.domain.execution.definition import (
    DefinitionIntegrityError,
    DefinitionManifest,
    DefinitionPin,
    DefinitionUnavailable,
    WorkerCapabilities,
)
from app.domain.execution.family import RunFamily
from app.domain.execution.run import TERMINAL_STATUSES
from app.infrastructure.execution.definition_models import (
    admissions,
    changes,
    definitions,
    heads,
    workers,
)
from app.infrastructure.execution.effect_models import requests as effect_requests
from app.infrastructure.execution.models import ExecutionCommandInboxORM, ExecutionEventORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


class DefinitionRetirementBlocked(ConflictError):
    def __init__(self, references):
        self.references = references
        super().__init__("DEFINITION_NOT_DRAINED")


class PostgresDefinitionStore:
    def __init__(self, session):
        self.session = session

    async def register(self, manifest: DefinitionManifest) -> None:
        await self.session.execute(
            insert(definitions)
            .values(
                family=manifest.family.value,
                definition_version=manifest.definition_version,
                manifest_digest=manifest.digest,
                body=manifest.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
        )
        body = await self.session.scalar(
            select(definitions.c.body).where(
                definitions.c.family == manifest.family.value,
                definitions.c.definition_version == manifest.definition_version,
            )
        )
        if body != manifest.model_dump(mode="json"):
            raise DefinitionIntegrityError("published definition identity has different content")

    async def seed_head(self, manifest: DefinitionManifest) -> None:
        await self.register(manifest)
        await self.session.execute(
            insert(heads)
            .values(family=manifest.family.value, manifest_digest=manifest.digest)
            .on_conflict_do_nothing()
        )

    async def head(self, family: RunFamily, *, lock=False) -> DefinitionPin:
        query = (
            select(definitions.c.body, definitions.c.retired_at, definitions.c.manifest_digest)
            .select_from(
                heads.join(definitions, heads.c.manifest_digest == definitions.c.manifest_digest)
            )
            .where(heads.c.family == family.value)
        )
        if lock:
            query = query.with_for_update(read=True, of=definitions)
        row = (await self.session.execute(query)).first()
        if row is None or row.retired_at is not None:
            raise DefinitionUnavailable("no active execution definition head")
        value = DefinitionManifest.model_validate(row.body)
        if value.digest != row.manifest_digest:
            raise DefinitionIntegrityError("definition body digest mismatch")
        return value.pin

    async def manifest(self, pin: DefinitionPin) -> DefinitionManifest:
        body = await self.session.scalar(
            select(definitions.c.body).where(definitions.c.manifest_digest == pin.manifest_digest)
        )
        if body is None:
            raise DefinitionUnavailable("execution definition copy is unavailable")
        value = DefinitionManifest.model_validate(body)
        value.require_pin(pin)
        return value

    async def heartbeat(self, capabilities: WorkerCapabilities, *, ttl: timedelta) -> None:
        if not timedelta(seconds=3) <= ttl <= timedelta(minutes=5):
            raise ValueError("worker capability TTL outside operational bounds")
        for pin in capabilities.definitions:
            if not capabilities.supports(await self.manifest(pin)):
                raise DefinitionIntegrityError("worker advertises an incomplete definition closure")
        values = {
            "worker_id": capabilities.worker_id,
            "boot_id": capabilities.boot_id,
            "body": capabilities.model_dump(mode="json"),
            "last_seen_at": func.clock_timestamp(),
            "expires_at": func.clock_timestamp() + ttl,
        }
        await self.session.execute(
            insert(workers)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[workers.c.worker_id, workers.c.boot_id],
                set_={"last_seen_at": values["last_seen_at"], "expires_at": values["expires_at"]},
                where=workers.c.body == values["body"],
            )
        )
        body = await self.session.scalar(
            select(workers.c.body).where(
                workers.c.worker_id == capabilities.worker_id,
                workers.c.boot_id == capabilities.boot_id,
            )
        )
        if body != values["body"]:
            raise DefinitionIntegrityError("worker boot identity cannot change its capabilities")

    async def runnable(self, manifest: DefinitionManifest) -> bool:
        bodies = (
            await self.session.scalars(
                select(workers.c.body).where(workers.c.expires_at > func.clock_timestamp())
            )
        ).all()
        return any(WorkerCapabilities.model_validate(body).supports(manifest) for body in bodies)

    async def pin_admission(
        self, *, family: RunFamily, scope_key: str, command_id: UUID, run_id: UUID
    ) -> DefinitionPin:
        # Serialize repeated request ids before reading a mutable head. The row
        # and CreateRun inbox insertion commit together in the caller's UoW.
        row = await self.session.scalar(
            text("SELECT public.opencitadel_pin_definition(:scope,:command,:run,:family)"),
            {"scope": scope_key, "command": command_id, "run": run_id, "family": family.value},
        )
        value = DefinitionManifest.model_validate(row["manifest"])
        if value.digest != row["digest"]:
            raise DefinitionIntegrityError("admission definition body digest mismatch")
        return value.pin

    async def publish_head(
        self,
        pin: DefinitionPin,
        *,
        expected_digest: str,
        actor_user_id: str,
        request_id: str,
        reason: str,
    ) -> DefinitionPin:
        if not reason.strip() or len(reason) > 2000:
            raise ValueError("definition change requires a bounded reason")
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"definition-change:{UUID(request_id)}"},
        )
        identity = {
            "actor_user_id": actor_user_id,
            "kind": "head",
            "family": pin.family.value,
            "expected_digest": expected_digest,
            "manifest_digest": pin.manifest_digest,
            "reason": reason.strip(),
        }
        previous = (
            (
                await self.session.execute(
                    select(changes).where(changes.c.request_id == UUID(request_id))
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if any(previous[key] != value for key, value in identity.items()):
                raise ConflictError("DEFINITION_REQUEST_CONFLICT")
            return pin
        await self.session.execute(
            select(heads.c.family).where(heads.c.family == pin.family.value).with_for_update()
        )
        target = (
            (
                await self.session.execute(
                    select(definitions).where(definitions.c.manifest_digest == pin.manifest_digest)
                )
            )
            .mappings()
            .first()
        )
        if target is None or target["retired_at"] is not None:
            raise ConflictError("DEFINITION_UNAVAILABLE")
        manifest = DefinitionManifest.model_validate(target["body"])
        manifest.require_pin(pin)
        if not await self.runnable(manifest):
            raise ConflictError("VERSION_UNAVAILABLE")
        changed = await self.session.scalar(
            update(heads)
            .where(heads.c.family == pin.family.value, heads.c.manifest_digest == expected_digest)
            .values(
                manifest_digest=pin.manifest_digest,
                revision=heads.c.revision + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(heads.c.family)
        )
        if changed is None:
            raise ConflictError("DEFINITION_HEAD_CONFLICT")
        await self.session.execute(insert(changes).values(request_id=UUID(request_id), **identity))
        return pin

    async def retirement_references(self, pin: DefinitionPin) -> dict:
        """Replay formal references in bounded pages; no readiness projection authority."""
        counts = {
            "active_runs": 0,
            "approval_waits": 0,
            "retry_waits": 0,
            "unsettled_effects": 0,
            "pending_admissions": 0,
            "pending_effect_requests": 0,
        }
        examples = []
        origin = ExecutionEventORM
        query = select(origin.stream_id, origin.owner_user_id, origin.team_id).where(
            origin.stream_type == "run",
            origin.stream_version == 1,
            origin.internal_payload["definition_pin"]["manifest_digest"].astext
            == pin.manifest_digest,
        )
        # Fence formal state and pending obligations under the established
        # scope append locks. Different families acquire scopes in the same order.
        scope_cursor = None
        while True:
            scope_query = (
                select(origin.owner_scope_key, origin.owner_user_id, origin.team_id)
                .where(
                    origin.stream_type == "run",
                    origin.stream_version == 1,
                    origin.internal_payload["definition_pin"]["manifest_digest"].astext
                    == pin.manifest_digest,
                )
                .distinct()
                .order_by(origin.owner_scope_key)
                .limit(200)
            )
            if scope_cursor is not None:
                scope_query = scope_query.where(origin.owner_scope_key > scope_cursor)
            scopes = (await self.session.execute(scope_query)).all()
            if not scopes:
                break
            for scope in scopes:
                await self.session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {
                        "key": PostgresEventStore._scope_advisory_lock_key(
                            scope.owner_user_id, scope.team_id
                        )
                    },
                )
            scope_cursor = scopes[-1].owner_scope_key
        cursor = None
        loader = PostgresVerifiedRunLoader(self.session)
        while True:
            page = query.order_by(origin.stream_id).limit(200)
            if cursor is not None:
                page = page.where(origin.stream_id > cursor)
            rows = (await self.session.execute(page)).all()
            if not rows:
                break
            for row in rows:
                loaded = await loader.load(
                    row.stream_id, owner_scope=(row.owner_user_id, row.team_id), required_pin=pin
                )
                state = loaded.replay.state
                active = state.status not in TERMINAL_STATUSES
                unsettled = sum(
                    item.state not in {"applied", "not_applied"}
                    or item.awaiting_result
                    or item.integrity_conflict
                    for item in state.effect_operations
                )
                counts["active_runs"] += int(active)
                if not active and state.active_activity_ids:
                    counts["active_runs"] += 1
                counts["approval_waits"] += int(state.pending_approval_id is not None)
                counts["retry_waits"] += int(active and state.wait_reason == "retry")
                counts["unsettled_effects"] += unsettled
                if (active or unsettled) and len(examples) < 20:
                    examples.append(row.stream_id)
            cursor = rows[-1].stream_id
        inbox = ExecutionCommandInboxORM
        counts["pending_admissions"] = (
            await self.session.scalar(
                select(func.count())
                .select_from(
                    admissions.outerjoin(inbox, admissions.c.command_id == inbox.command_id)
                )
                .where(
                    admissions.c.manifest_digest == pin.manifest_digest,
                    (inbox.status.is_(None))
                    | inbox.status.in_(("received", "processing", "dead_lettered")),
                )
            )
            or 0
        )
        counts["pending_effect_requests"] = (
            await self.session.scalar(
                select(func.count())
                .select_from(
                    effect_requests.join(
                        origin,
                        effect_requests.c.run_id.cast(origin.stream_id.type) == origin.stream_id,
                    )
                )
                .where(
                    origin.stream_type == "run",
                    origin.stream_version == 1,
                    origin.internal_payload["definition_pin"]["manifest_digest"].astext
                    == pin.manifest_digest,
                    effect_requests.c.scope_key == origin.owner_scope_key,
                    effect_requests.c.status.in_(("pending", "running")),
                )
            )
            or 0
        )
        return {"counts": counts, "run_ids": examples}

    async def retire(
        self, pin: DefinitionPin, *, actor_user_id: str, request_id: str, reason: str
    ) -> DefinitionPin:
        if not reason.strip() or len(reason) > 2000:
            raise ValueError("definition retirement requires a bounded reason")
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"definition-change:{UUID(request_id)}"},
        )
        identity = {
            "actor_user_id": actor_user_id,
            "kind": "retire",
            "family": pin.family.value,
            "expected_digest": pin.manifest_digest,
            "manifest_digest": pin.manifest_digest,
            "reason": reason.strip(),
        }
        previous = (
            (
                await self.session.execute(
                    select(changes).where(changes.c.request_id == UUID(request_id))
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if any(previous[key] != value for key, value in identity.items()):
                raise ConflictError("DEFINITION_REQUEST_CONFLICT")
            return pin
        head_digest = await self.session.scalar(
            select(heads.c.manifest_digest)
            .where(heads.c.family == pin.family.value)
            .with_for_update()
        )
        if head_digest == pin.manifest_digest:
            raise ConflictError("DEFINITION_IS_CURRENT_HEAD")
        row = (
            (
                await self.session.execute(
                    select(definitions)
                    .where(definitions.c.manifest_digest == pin.manifest_digest)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DefinitionUnavailable("execution definition is unavailable")
        DefinitionManifest.model_validate(row["body"]).require_pin(pin)
        if row["retired_at"] is not None:
            raise ConflictError("DEFINITION_ALREADY_RETIRED")
        references = await self.retirement_references(pin)
        if any(references["counts"].values()):
            raise DefinitionRetirementBlocked(references)
        await self.session.execute(
            update(definitions)
            .where(definitions.c.manifest_digest == pin.manifest_digest)
            .values(retired_at=func.clock_timestamp())
        )
        await self.session.execute(insert(changes).values(request_id=UUID(request_id), **identity))
        return pin
