"""Typed, current-caller-bound capture of safe immutable read metadata."""

import hashlib
import hmac
import json
import time
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
from app.infrastructure.execution.query_observation import named_query
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


class DBEvaluationSummaryRepository:
    def __init__(self, work, *, signing_secret):
        self.work, self.db, self.secret = work, work.db_session, signing_secret

    async def _read(self, scope, principal, batch_id, **query):
        await self.work.evaluation_dataset.authorize(scope, principal, write=False)
        body = json.dumps(
            {
                "scope": params(scope)["scope"],
                "principal": principal.model_dump(mode="json"),
                "batch_id": str(batch_id),
                "expires": time.time() + 30,
                "authorization_signature": await self.db.scalar(
                    text("SELECT current_setting('app.auth_signature',true)")
                ),
                **query,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        signature = hmac.new(
            self.secret.encode(), ("opencitadel:e11:snapshot:v1:" + body).encode(), hashlib.sha256
        ).hexdigest()
        try:
            return await self.db.scalar(
                named_query(
                    text("SELECT public.opencitadel_e11_snapshot(:body,:signature)"),
                    "analysis.scores",
                ),
                {"body": body, "signature": signature},
            )
        except DBAPIError as exc:
            reason = str(exc.orig).splitlines()[0].rsplit(": ", 1)[-1]
            if "summary_authorization" in reason:
                raise PermissionError("evaluation permission denied") from None
            if "summary_not_found" in reason:
                raise DatasetNotFound("summary_not_found") from None
            if "summary_expired" in reason or "summary_capacity" in reason:
                raise DatasetConflict("summary_refresh_required") from None
            if "summary_" in reason:
                raise ValueError("summary_query_unavailable") from None
            raise RuntimeError("summary_storage_unavailable") from None

    async def capture(
        self, scope, principal, batch_id, *, source, dimension, rubric_id, evaluation_revision=None
    ):
        if (
            source not in {"rule", "human", "model"}
            or not isinstance(dimension, str)
            or not 1 <= len(dimension) <= 255
        ):
            raise ValueError("invalid_summary_query")
        if evaluation_revision is not None and (
            type(evaluation_revision) is not int or evaluation_revision < 0
        ):
            raise ValueError("invalid_summary_revision")
        return await self._read(
            scope,
            principal,
            batch_id,
            operation="capture",
            source=source,
            dimension=dimension,
            rubric_id=str(UUID(str(rubric_id))),
            evaluation_revision=evaluation_revision,
        )

    async def get(self, scope, principal, batch_id, snapshot_id):
        return await self._read(
            scope, principal, batch_id, operation="get", snapshot_id=str(UUID(str(snapshot_id)))
        )

    async def list_batches(self, scope, *, after, limit):
        return (
            (
                await self.db.execute(
                    text("""SELECT b.id,v.name,b.suite_version,b.status,b.revision,b.created_at,COALESCE(h.revision,0) AS evaluation_revision
            FROM evaluation_batches b JOIN evaluation_suite_versions v ON v.scope_key=b.scope_key AND v.id=b.suite_version
            LEFT JOIN evaluation_score_heads h ON h.scope_key=b.scope_key AND h.batch_id=b.id
            WHERE b.scope_key=:scope AND NOT EXISTS(SELECT 1 FROM evaluation_resource_archives a WHERE a.scope_key=b.scope_key AND a.kind='batch' AND a.resource_id=b.id) AND (CAST(:after AS uuid) IS NULL OR b.id>CAST(:after AS uuid)) ORDER BY b.id LIMIT :limit"""),
                    params(scope, after=after, limit=limit),
                )
            )
            .mappings()
            .all()
        )

    async def events(self, scope, batch_id, *, after):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT revision,kind FROM evaluation_batch_events WHERE scope_key=:scope AND batch_id=:batch AND revision>:after ORDER BY revision LIMIT 200"
                    ),
                    params(scope, batch=batch_id, after=after),
                )
            )
            .mappings()
            .all()
        )

    async def cleanup_expired(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        return await self.db.scalar(
            text("SELECT public.opencitadel_e11_cleanup(:limit)"), {"limit": limit}
        )

    async def invalidations(self, scope, principal, batch_id, *, evaluation_revision):
        return await self._read(
            scope,
            principal,
            batch_id,
            operation="invalidations",
            evaluation_revision=evaluation_revision,
        )
