"""Durable prewrites, publication fences and bounded deletion outside transactions."""

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.domain.external.object_storage import ObjectNotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.security.db_authorization import configure_session_authorization

logger = logging.getLogger(__name__)

PRIVATE_PREFIXES = tuple(
    "execution/" + name + "/"
    for name in ("inputs", "results", "context_documents", "context_sources")
)


async def guard_object_reference(session, scope_key, run_id):
    if not await session.scalar(
        text("SELECT public.opencitadel_object_reference_guard(:scope,:run)"),
        {"scope": scope_key, "run": run_id},
    ):
        raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")


class ExecutionObjectLifecycle:
    def __init__(self, sessions, storage, *, authorization, signing_secret):
        self.sessions, self.storage = sessions, storage
        self.authorization, self.secret = authorization, signing_secret

    async def _authorize(self, db):
        await configure_session_authorization(db, self.authorization, signing_secret=self.secret)

    async def put(self, key, body, *, run_id, scope_key=None):
        if run_id is None or not key.startswith(PRIVATE_PREFIXES):
            raise ValueError("CONTEXT_OBJECT_IDENTITY_INVALID")
        async with self.sessions() as db:
            await self._authorize(db)
            registered = await db.scalar(
                text("SELECT public.opencitadel_object_register(:scope,:run,:key,:digest)"),
                {
                    "scope": scope_key,
                    "run": run_id,
                    "key": key,
                    "digest": hashlib.sha256(body).hexdigest(),
                },
            )
            if not registered:
                raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
            # Commit before external I/O, independently of the admission UoW.
            await db.commit()
        await self.storage.put_bytes(key, body)
        async with self.sessions() as db:
            await self._authorize(db)
            if not await db.scalar(
                text("SELECT public.opencitadel_object_ready(:key)"), {"key": key}
            ):
                raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
            await db.commit()

    async def available(self, key):
        async with self.sessions() as db:
            await self._authorize(db)
            if not await db.scalar(
                text("SELECT public.opencitadel_object_available(:key)"), {"key": key}
            ):
                raise ObjectNotFoundError("retained execution object unavailable")


class RetainedExecutionStorage:
    """All production private reads check availability; other namespaces pass through."""

    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.storage = lifecycle.storage

    async def put_execution_bytes(self, key, body, *, run_id, scope_key=None):
        await self.lifecycle.put(key, body, run_id=run_id, scope_key=scope_key)

    async def guard_publication(self, session, scope_key, run_id):
        await guard_object_reference(session, scope_key, run_id)

    async def put_bytes(self, key, body):
        if key.startswith(PRIVATE_PREFIXES):
            raise ValueError("CONTEXT_OBJECT_OWNER_REQUIRED")
        await self.storage.put_bytes(key, body)

    async def get_bytes(self, key):
        if key.startswith(PRIVATE_PREFIXES):
            await self.lifecycle.available(key)
        return await self.storage.get_bytes(key)

    async def get_bounded_bytes(self, key, limit):
        if key.startswith(PRIVATE_PREFIXES):
            await self.lifecycle.available(key)
        return await self.storage.get_bounded_bytes(key, limit)

    async def delete_bytes(self, key):
        if key.startswith(PRIVATE_PREFIXES):
            raise ValueError("CONTEXT_OBJECT_RETENTION_REQUIRED")
        await self.storage.delete_bytes(key)


class ExecutionObjectGC:
    def __init__(
        self,
        sessions,
        storage,
        *,
        signing_secret,
        retention_days=30,
        orphan_hours=24,
        batch_size=100,
    ):
        if retention_days < 1 or orphan_hours < 1 or not 1 <= batch_size <= 1000:
            raise ValueError("invalid execution object retention policy")
        self.sessions, self.storage, self.secret = sessions, storage, signing_secret
        self.retention = timedelta(days=retention_days)
        self.grace = timedelta(hours=orphan_hours)
        self.batch_size = batch_size

    async def _authorize(self, db):
        await configure_session_authorization(
            db, AuthorizationContext.system("execution-kernel"), signing_secret=self.secret
        )

    async def cleanup(self, *, now=None):
        now = now or datetime.now(UTC)
        from app.infrastructure.execution.execution_object_roots import collection_is_retained
        from app.infrastructure.execution.postgres_event_store import PostgresEventStore

        marked = deleted = errors = 0
        # The number of candidate runs, object DELETEs, and transaction time are
        # bounded independently. checked_at rotates protected roots fairly.
        async with self.sessions() as db:
            await self._authorize(db)
            candidates = (
                await db.execute(
                    text("""
SELECT run_id,scope_key FROM execution_object_runs WHERE state='live' AND created_at<:before
ORDER BY checked_at NULLS FIRST,run_id LIMIT :limit
"""),
                    {"before": now - self.grace, "limit": min(self.batch_size, 10)},
                )
            ).all()
        for run_id, scope in candidates:
            try:
                async with asyncio.timeout(30), self.sessions() as db:
                    await self._authorize(db)
                    owner, team = (
                        (scope[5:], None) if scope.startswith("user:") else (None, scope[5:])
                    )
                    locked = await db.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:key)"),
                        {"key": PostgresEventStore._scope_advisory_lock_key(owner, team)},
                    )
                    if not locked:
                        continue
                    row = (
                        (
                            await db.execute(
                                text(
                                    "SELECT * FROM execution_object_runs WHERE run_id=:run AND state='live' FOR UPDATE"
                                ),
                                {"run": run_id},
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if row is None:
                        continue
                    retained, reason = await collection_is_retained(
                        db, row, now=now, retention=self.retention, grace=self.grace
                    )
                    await db.execute(
                        text(
                            "UPDATE execution_object_runs SET checked_at=:now,reason=:reason WHERE run_id=:run"
                        ),
                        {"now": now, "reason": reason, "run": run_id},
                    )
                    if not retained:
                        await db.execute(
                            text(
                                "UPDATE execution_object_runs SET state='deleting',deleting_at=:now WHERE run_id=:run"
                            ),
                            {"run": run_id, "now": now},
                        )
                        marked += 1
                    # Tombstone precedes DELETE. A timeout cannot reopen publication.
                    await db.commit()
            except Exception:
                # Fail closed for this collection, but rotate it so corruption or
                # a slow provider cannot starve unrelated retention work.
                errors += 1
                logger.exception("Execution object retention check failed for %s", run_id)
                async with self.sessions() as db:
                    await self._authorize(db)
                    await db.execute(
                        text(
                            "UPDATE execution_object_runs SET checked_at=:now,reason='check_failed' WHERE run_id=:run AND state='live'"
                        ),
                        {"now": now, "run": run_id},
                    )
                    await db.commit()
        async with self.sessions() as db:
            await self._authorize(db)
            objects = (
                await db.execute(
                    text("""
SELECT o.object_key,o.run_id FROM execution_objects o JOIN execution_object_runs r USING(run_id,scope_key)
WHERE r.state IN ('deleting','deleted') AND (o.cleaned_at IS NULL OR o.cleaned_at<:before)
AND (o.delete_attempt_at IS NULL OR o.delete_attempt_at<:retry)
ORDER BY o.cleaned_at NULLS FIRST,o.object_key LIMIT :limit
"""),
                    {
                        "before": now - timedelta(hours=1),
                        "retry": now - timedelta(minutes=1),
                        "limit": self.batch_size,
                    },
                )
            ).all()
        for key, run_id in objects:
            async with self.sessions() as db:
                await self._authorize(db)
                claimed = await db.execute(
                    text(
                        "UPDATE execution_objects SET delete_attempt_at=:now WHERE object_key=:key AND (delete_attempt_at IS NULL OR delete_attempt_at<:retry)"
                    ),
                    {"now": now, "retry": now - timedelta(minutes=1), "key": key},
                )
                await db.commit()
                if not claimed.rowcount:
                    continue
            try:
                async with asyncio.timeout(30):
                    with suppress(ObjectNotFoundError):
                        await self.storage.delete_bytes(key)
            except Exception:
                errors += 1
                logger.exception("Execution object deletion failed for run %s", run_id)
                continue
            async with self.sessions() as db:
                await self._authorize(db)
                await db.execute(
                    text("UPDATE execution_objects SET cleaned_at=:now WHERE object_key=:key"),
                    {"now": now, "key": key},
                )
                await db.execute(
                    text("""
UPDATE execution_object_runs r SET state='deleted' WHERE run_id=:run AND state='deleting'
AND NOT EXISTS(SELECT 1 FROM execution_objects o WHERE o.run_id=r.run_id AND o.cleaned_at IS NULL)
"""),
                    {"run": run_id},
                )
                await db.commit()
            deleted += 1
        # Keep tombstones and revisit them hourly: an in-flight PUT from a lost
        # worker may land after DELETE, but can never become ready or published.
        return {"marked_runs": marked, "deleted_objects": deleted, "errors": errors}
