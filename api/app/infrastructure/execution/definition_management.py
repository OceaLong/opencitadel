"""API-owned ingress and kernel-owned execution for global definition changes."""

import json
from uuid import UUID

from sqlalchemy import text

from app.application.execution.definition_management import DefinitionChange
from app.domain.errors import ConflictError, NotFoundError
from app.domain.execution.definition import DefinitionManifest
from app.infrastructure.execution.postgres_definition_store import (
    DefinitionRetirementBlocked,
    PostgresDefinitionStore,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresDefinitionManagement:
    def __init__(self, *, session_factory, authorization):
        self.sessions, self.authorization = session_factory, authorization

    async def _rows(self, query, params):
        async with self.sessions() as db:
            await configure_session_authorization(db, self.authorization)
            return [dict(row) for row in (await db.execute(text(query), params)).mappings()]

    async def definitions(self, *, limit, offset):
        rows = await self._rows(
            """
SELECT d.family,d.definition_version,d.manifest_digest,d.created_at,d.retired_at,
 d.body->>'planner_version' AS planner_version,d.body->>'compiler_version' AS compiler_version,
 COALESCE(h.manifest_digest=d.manifest_digest,false) AS is_head,h.revision AS head_revision,h.manifest_digest AS head_digest
 FROM execution_definitions d LEFT JOIN execution_definition_heads h ON h.family=d.family
 ORDER BY d.family,d.definition_version LIMIT :limit OFFSET :offset
""",
            {"limit": limit + 1, "offset": offset},
        )
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}

    async def workers(self, *, limit, offset):
        rows = await self._rows(
            """
SELECT worker_id,boot_id,last_seen_at,expires_at,expires_at>clock_timestamp() AS available,
 body->'definitions' AS definitions,body->>'build_artifact_digest' AS build_artifact_digest
 FROM execution_worker_capabilities ORDER BY worker_id,boot_id LIMIT :limit OFFSET :offset
""",
            {"limit": limit + 1, "offset": offset},
        )
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}

    async def submit(self, actor, request):
        body = request.model_dump(mode="json")
        async with self.sessions() as db:
            await configure_session_authorization(db, self.authorization)
            await db.execute(
                text("""
INSERT INTO execution_definition_requests(request_id,actor_user_id,body)
 VALUES(:id,:actor,CAST(:body AS jsonb)) ON CONFLICT(request_id) DO NOTHING
"""),
                {"id": request.request_id, "actor": actor, "body": json.dumps(body)},
            )
            row = (
                (
                    await db.execute(
                        text("SELECT * FROM execution_definition_requests WHERE request_id=:id"),
                        {"id": request.request_id},
                    )
                )
                .mappings()
                .one()
            )
            if row["actor_user_id"] != actor or row["body"] != body:
                raise ConflictError("DEFINITION_REQUEST_CONFLICT")
            await db.commit()
            return dict(row)

    async def result(self, request_id):
        rows = await self._rows(
            "SELECT request_id,status,result FROM execution_definition_requests WHERE request_id=:id",
            {"id": UUID(str(request_id))},
        )
        if not rows:
            raise NotFoundError("definition request unavailable")
        return rows[0]

    async def process_pending(self, *, limit=20):
        emitted = 0
        for _ in range(limit):
            async with self.sessions() as db:
                await configure_session_authorization(db, self.authorization)
                row = (
                    (
                        await db.execute(
                            text("""
SELECT * FROM execution_definition_requests WHERE status='pending'
 ORDER BY created_at,request_id LIMIT 1 FOR UPDATE SKIP LOCKED
""")
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    break
                actor = await db.scalar(
                    text("""
SELECT id FROM users WHERE id=:actor AND global_role='admin' AND status='active' FOR SHARE
"""),
                    {"actor": row["actor_user_id"]},
                )
                status, result = "failed", {"code": "DEFINITION_ACTOR_REVOKED"}
                if actor:
                    try:
                        async with db.begin_nested():
                            request = DefinitionChange.model_validate(row["body"])
                            if request.request_id != row["request_id"]:
                                raise ValueError("request identity differs")
                            body = await db.scalar(
                                text("""
SELECT body FROM execution_definitions WHERE family=:family AND definition_version=:version
"""),
                                {"family": request.family.value, "version": request.target_version},
                            )
                            if body is None:
                                raise ConflictError("DEFINITION_UNAVAILABLE")
                            manifest = DefinitionManifest.model_validate(body)
                            store = PostgresDefinitionStore(db)
                            args = {
                                "actor_user_id": actor,
                                "request_id": str(request.request_id),
                                "reason": request.reason,
                            }
                            if request.kind == "head":
                                await store.publish_head(
                                    manifest.pin, expected_digest=request.expected_digest, **args
                                )
                            else:
                                if manifest.digest != request.expected_digest:
                                    raise ConflictError("DEFINITION_MANIFEST_CONFLICT")
                                await store.retire(manifest.pin, **args)
                            status, result = (
                                "completed",
                                {"pin": manifest.pin.model_dump(mode="json")},
                            )
                    except DefinitionRetirementBlocked as error:
                        result = {
                            "code": "DEFINITION_RETIREMENT_BLOCKED",
                            "references": error.references,
                        }
                    except ConflictError as error:
                        result = {"code": str(error)}
                    except ValueError:
                        result = {"code": "DEFINITION_REQUEST_INVALID"}
                await db.execute(
                    text("""
UPDATE execution_definition_requests SET status=:status,result=CAST(:result AS jsonb),completed_at=clock_timestamp()
 WHERE request_id=:id AND status='pending'
"""),
                    {"id": row["request_id"], "status": status, "result": json.dumps(result)},
                )
                await db.commit()
                emitted += 1
        return {"emitted": emitted}
