"""Dedicated bounded-pool recording intents; durable bytes precede publication."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.domain.evaluation.errors import DatasetConflict
from app.domain.evaluation.recording import MAX_RECORDING_BYTES
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository,
    params,
)
from app.infrastructure.repositories.db_evaluation_recording_repository import (
    DBEvaluationRecordingRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class RecordingObjectLifecycle:
    def __init__(self, session_factory, objects, *, signing_secret):
        self.factory, self.objects, self.secret = session_factory, objects, signing_secret

    async def put(self, authorization, *, job_id, claim_token, body):
        if authorization.principal is None or authorization.scope is None:
            raise PermissionError("recording upload requires principal")
        if not 0 < len(body) <= MAX_RECORDING_BYTES:
            raise ValueError("recording_result_too_large")
        scope = authorization.scope
        identity, digest = uuid4(), hashlib.sha256(body).hexdigest()
        # Dynamic storage object key, not a credential; UUID supplies the object identity.
        key = "evaluation/recordings/" + str(identity)  # gitleaks:allow
        async with asyncio.timeout(60), self.factory() as db:
            await configure_session_authorization(db, authorization, signing_secret=self.secret)
            await DBEvaluationDatasetRepository(db).authorize(
                scope, authorization.principal, write=True
            )
            job = await DBEvaluationRecordingRepository(db).job(scope, job_id)
            if (
                job["claim_token"] != claim_token
                or job["status"] != "running"
                or job["lease_until"] <= datetime.now(UTC)
            ):
                raise DatasetConflict("recording_claim_lost")
            await db.execute(
                text(
                    "INSERT INTO evaluation_recording_objects(id,job_id,storage_key,digest,size_bytes,owner_user_id,team_id,created_by) VALUES(:id,:job,:key,:digest,:size,:owner,:team,:actor)"
                ),
                params(scope, id=identity, job=job_id, key=key, digest=digest, size=len(body)),
            )
            await db.commit()
            await configure_session_authorization(db, authorization, signing_secret=self.secret)
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "recording-object:" + str(identity)},
            )
            await self.objects.put_bytes(key, body)
            if await self.objects.get_bytes(key) != body:
                raise ValueError("recording_upload_changed")
            await db.commit()
        return identity, digest, len(body)

    async def cleanup(self, *, limit=100, now=None):
        now = now or datetime.now(UTC)
        auth = AuthorizationContext.system("recording-object-cleanup")
        predicate = "o.cleaned_at IS NULL AND o.created_at<:before AND NOT EXISTS (SELECT 1 FROM evaluation_recording_slots s WHERE s.scope_key=o.scope_key AND s.object_id=o.id) AND NOT EXISTS (SELECT 1 FROM evaluation_recording_jobs j WHERE j.scope_key=o.scope_key AND j.id=o.job_id AND j.status='running' AND j.lease_until>:now)"
        async with asyncio.timeout(30), self.factory() as db:
            await configure_session_authorization(db, auth, signing_secret=self.secret)
            rows = (
                await db.execute(
                    text(
                        f"SELECT id,scope_key FROM evaluation_recording_objects o WHERE {predicate} ORDER BY updated_at LIMIT :limit"
                    ),
                    {"before": now - timedelta(hours=1), "now": now, "limit": min(limit, 100)},
                )
            ).all()
            await db.rollback()
            cleaned = 0
            for identity, scope_key in rows:
                await configure_session_authorization(db, auth, signing_secret=self.secret)
                if not await db.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": "recording-object:" + str(identity)},
                ):
                    await db.rollback()
                    continue
                key = await db.scalar(
                    text(
                        f"SELECT storage_key FROM evaluation_recording_objects o WHERE scope_key=:scope AND id=:id AND {predicate} FOR UPDATE"
                    ),
                    {
                        "scope": scope_key,
                        "id": identity,
                        "before": now - timedelta(hours=1),
                        "now": now,
                    },
                )
                if key:
                    await self.objects.delete_bytes(key)
                    await db.execute(
                        text(
                            "UPDATE evaluation_recording_objects SET cleaned_at=:now,updated_at=:now WHERE scope_key=:scope AND id=:id"
                        ),
                        {"scope": scope_key, "id": identity, "now": now},
                    )
                    cleaned += 1
                await db.commit()
            return cleaned
