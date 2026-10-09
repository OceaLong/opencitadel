"""Caller-owned transactions: publication and pins commit or roll back together."""

from uuid import uuid4

from sqlalchemy import text

from app.domain.models.resource_pin import (
    PinValidation,
    ResourceIdentity,
    ResourcePinned,
    ResourceUnavailable,
)
from app.infrastructure.execution.original_evidence import retain_read, session_evidence


def scope_params(scope):
    return {
        "owner": scope.user_id if scope.team_id is None else None,
        "team": scope.team_id,
        "scope": "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id,
    }


_SCOPE = "owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"
# A team's session retains its creator in owner_user_id. Team membership is
# enforced by the request/RLS boundary; the resource must match that exact team.
_SESSION_SCOPE = (
    "team_id IS NOT DISTINCT FROM :team "
    "AND (:team IS NOT NULL OR owner_user_id IS NOT DISTINCT FROM :owner)"
)


def require_resource_owner(value):
    if not value:
        raise ResourceUnavailable("resource unavailable")


def artifact_version_number(value):
    if not value.isdecimal() or int(value) < 1 or str(int(value)) != value:
        raise ResourceUnavailable("invalid fixed artifact version")
    return int(value)


async def require_resolved_resource(scope, resource, row, *, file_repository, knowledge_repository):
    if row and resource.resource_kind == "execution_content":
        from app.domain.services.content_source_authority import content_source_available

        for citation in row.citation_refs:
            if not await content_source_available(
                scope,
                citation,
                file_repository=file_repository,
                knowledge_repository=knowledge_repository,
            ):
                raise ResourceUnavailable("content dependency unavailable")
    if not row:
        raise ResourceUnavailable("fixed resource unavailable")
    return row


async def validate_original_pin(scope, resource, row, repository):
    reason = "missing_pin" if row is None else row.unavailable_reason
    if row is not None and row.available:
        try:
            await repository.resolve(scope, resource)
        except ResourceUnavailable:
            reason = "resource_unavailable"
    return PinValidation(resource=resource, available=reason is None, reason=reason)


class DBResourcePinRepository:
    def __init__(self, db_session, *, owner_validators=None):
        self.db_session = db_session
        # Trusted composition may register a future publication owner validator.
        # It receives this SAME DB transaction; it must check scoped persisted or
        # staged owner authority. No fallback accepts a merely supplied owner ID.
        self.owner_validators = owner_validators or {}

    async def _owner(self, scope, kind, identity):
        await self.db_session.flush()
        if kind in self.owner_validators:
            if await self.owner_validators[kind](self.db_session, scope, identity):
                return
        elif kind in ("session", "run"):
            table, column = (
                ("sessions", "id") if kind == "session" else ("execution_view_runs", "run_id")
            )
            if await self.db_session.scalar(
                text(
                    f"SELECT 1 FROM {table} WHERE CAST({column} AS text)=:id "
                    f"AND {_SESSION_SCOPE if kind == 'session' else _SCOPE}"
                ),
                {**scope_params(scope), "id": identity},
            ):
                return
        raise ResourceUnavailable("pin owner unavailable")

    async def resolve(self, scope, resource, *, lock=False):
        p = {
            **scope_params(scope),
            "id": resource.resource_id,
            "version": resource.resource_version,
        }
        suffix = " FOR UPDATE" if lock else ""
        if resource.resource_kind == "knowledge_base":
            owner_result = await self.db_session.execute(
                text(
                    f"SELECT id FROM knowledge_bases WHERE id=:id AND {_SCOPE} AND deleted_at IS NULL"
                    + suffix
                ),
                p,
            )
            try:
                resource_row = owner_result.scalar()
                retain_read(
                    self.db_session,
                    "resource-source",
                    "pins.knowledge_owner",
                    p,
                    resource_row,
                    source_result=owner_result,
                )
            finally:
                owner_result.close()
                synchronous = getattr(self.db_session, "sync_session", self.db_session)
                forget_result = getattr(synchronous, "forget_result", None)
                if callable(forget_result):
                    forget_result(owner_result)
            require_resource_owner(resource_row)
            result = await self.db_session.execute(
                text(
                    "SELECT id FROM knowledge_base_versions WHERE knowledge_base_id=:id AND id=:version AND published_at IS NOT NULL AND state IN ('ready','degraded')"
                    + suffix
                ),
                p,
            )
        elif resource.resource_kind == "artifact":
            # Same session barrier as purge; then the F05 before-load writer lock.
            artifact_result = await self.db_session.execute(
                text("SELECT session_id FROM artifacts WHERE id=:id"), p
            )
            try:
                session_id = artifact_result.scalar()
                retain_read(
                    self.db_session,
                    "resource-source",
                    "pins.artifact_session",
                    p,
                    session_id,
                    source_result=artifact_result,
                )
            finally:
                artifact_result.close()
                synchronous = getattr(self.db_session, "sync_session", self.db_session)
                forget_result = getattr(synchronous, "forget_result", None)
                if callable(forget_result):
                    forget_result(artifact_result)
            require_resource_owner(session_id)
            session_result = await self.db_session.execute(
                text(
                    f"SELECT id FROM sessions WHERE id=:session AND {_SESSION_SCOPE} AND deleted_at IS NULL"
                    + (" FOR NO KEY UPDATE" if lock else "")
                ),
                {**p, "session": session_id},
            )
            try:
                owned = session_result.scalar()
                retain_read(
                    self.db_session,
                    "resource-source",
                    "pins.session_owner",
                    {**p, "session": session_id},
                    owned,
                    source_result=session_result,
                )
            finally:
                session_result.close()
                synchronous = getattr(self.db_session, "sync_session", self.db_session)
                forget_result = getattr(synchronous, "forget_result", None)
                if callable(forget_result):
                    forget_result(session_result)
            require_resource_owner(owned)
            if lock:
                await self.db_session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": "artifact-version:" + resource.resource_id},
                )
            artifact_version_number(resource.resource_version)
            result = await self.db_session.execute(
                text(
                    "SELECT version_refs->>(:number-1) AS storage_key FROM artifacts WHERE id=:id AND session_id=:session AND jsonb_array_length(version_refs)>=:number"
                    + suffix
                ),
                {**p, "number": int(resource.resource_version), "session": session_id},
            )
        elif resource.resource_kind == "file":
            result = await self.db_session.execute(
                text(
                    f"SELECT key,content_digest FROM files WHERE id=:id AND {_SCOPE} AND content_digest=:version AND content_available"
                    + suffix
                ),
                p,
            )
        else:
            result = await self.db_session.execute(
                text(
                    f"SELECT content_id,citation_refs FROM execution_public_content WHERE content_id::text=:id AND {_SCOPE} AND content_digest=:version AND EXISTS (SELECT 1 FROM execution_content_bindings b WHERE b.content_id=execution_public_content.content_id AND b.scope_key=execution_public_content.scope_key)"
                ),
                p,
            )
        try:
            row = result.first()
            retain_read(
                self.db_session,
                "resource-source",
                "pins.resolve",
                {"scope": scope, "resource": resource, "lock": lock},
                row,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(self.db_session, "sync_session", self.db_session)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)
        from .db_file_repository import DBFileRepository
        from .db_knowledge_base_repository import DBKnowledgeBaseRepository

        return await require_resolved_resource(
            scope,
            resource,
            row,
            file_repository=DBFileRepository(self.db_session),
            knowledge_repository=DBKnowledgeBaseRepository(self.db_session),
        )

    async def acquire(self, scope, owner_kind, owner_id, resources):
        await self._owner(scope, owner_kind, owner_id)
        refs = sorted({(r.resource_kind, r.resource_id, r.resource_version) for r in resources})
        # Savepoint makes partial acquisition atomic even if a caller catches an error.
        async with self.db_session.begin_nested():
            for kind, identity, version in refs:
                resource = ResourceIdentity(
                    resource_kind=kind, resource_id=identity, resource_version=version
                )
                await self.resolve(scope, resource, lock=True)
                p = {
                    **scope_params(scope),
                    "id": uuid4(),
                    "kind": kind,
                    "resource": identity,
                    "version": version,
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "actor": scope.user_id,
                }
                existing = await self.db_session.scalar(
                    text(
                        "SELECT available FROM resource_pins WHERE scope_key=:scope AND owner_kind=:owner_kind AND owner_id=:owner_id AND resource_kind=:kind AND resource_id=:resource AND resource_version=:version"
                    ),
                    p,
                )
                if existing is False:
                    raise ResourceUnavailable("pin was invalidated")
                await self.db_session.execute(
                    text("""INSERT INTO resource_pins(id,owner_kind,owner_id,resource_kind,resource_id,resource_version,owner_user_id,team_id,created_by)
                VALUES (:id,:owner_kind,:owner_id,:kind,:resource,:version,:owner,:team,:actor)
                ON CONFLICT(scope_key,owner_kind,owner_id,resource_kind,resource_id,resource_version) DO NOTHING"""),
                    p,
                )

    async def release(self, scope, owner_kind, owner_id, resources):
        for resource in resources:
            await self.db_session.execute(
                text(
                    "DELETE FROM resource_pins WHERE scope_key=:scope AND owner_kind=:owner_kind AND owner_id=:owner_id AND resource_kind=:kind AND resource_id=:resource AND resource_version=:version"
                ),
                {
                    **scope_params(scope),
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "kind": resource.resource_kind,
                    "resource": resource.resource_id,
                    "version": resource.resource_version,
                },
            )

    async def validate(self, scope, owner_kind, owner_id, resources):
        evidence = session_evidence(self.db_session)
        if evidence is not None:
            evidence.reserve_state(len(resources))
        out = []
        for resource in resources:
            result = await self.db_session.execute(
                text(
                    "SELECT available,unavailable_reason FROM resource_pins WHERE scope_key=:scope AND owner_kind=:owner_kind AND owner_id=:owner_id AND resource_kind=:kind AND resource_id=:resource AND resource_version=:version"
                ),
                {
                    **scope_params(scope),
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "kind": resource.resource_kind,
                    "resource": resource.resource_id,
                    "version": resource.resource_version,
                },
            )
            try:
                row = result.first()
                retain_read(
                    self.db_session,
                    "resource-source",
                    "pins.validate",
                    {
                        "scope": scope,
                        "owner_kind": owner_kind,
                        "owner_id": owner_id,
                        "resource": resource,
                    },
                    row,
                    source_result=result,
                )
            finally:
                result.close()
                synchronous = getattr(self.db_session, "sync_session", self.db_session)
                forget_result = getattr(synchronous, "forget_result", None)
                if callable(forget_result):
                    forget_result(result)
            out.append(await validate_original_pin(scope, resource, row, self))
        return out

    async def guard_delete(self, kind, resource_id, *, force=False):
        # Caller holds resource/version (or session→artifact) barrier before this call.
        p = {"kind": kind, "id": resource_id}
        exists = await self.db_session.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM resource_pins WHERE resource_kind=:kind AND resource_id=:id AND available)"
            ),
            p,
        )
        if exists and not force:
            raise ResourcePinned("resource is pinned", resource_kind=kind, resource_id=resource_id)
        if exists:
            await self.db_session.execute(
                text(
                    "UPDATE resource_pins SET available=false,unavailable_at=CURRENT_TIMESTAMP,unavailable_reason='force_deleted',updated_at=CURRENT_TIMESTAMP WHERE resource_kind=:kind AND resource_id=:id AND available"
                ),
                p,
            )

    async def guard_session_purge(self, session_id, scope=None, *, force=False):
        p = {"id": session_id}
        clause = ""
        if scope:
            p.update(scope_params(scope))
            clause = " AND " + _SCOPE
        row = (
            await self.db_session.execute(
                text("SELECT id FROM sessions WHERE id=:id" + clause + " FOR NO KEY UPDATE"), p
            )
        ).first()
        if not row:
            return
        ids = (
            (
                await self.db_session.execute(
                    text("SELECT id FROM artifacts WHERE session_id=:id ORDER BY id"), p
                )
            )
            .scalars()
            .all()
        )
        for identity in ids:
            await self.db_session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "artifact-version:" + identity},
            )
            await self.guard_delete("artifact", identity, force=force)

    async def readable_evaluation_owners(self, scope, resource):
        """Only immutable public evaluation owners; no Run/session/private owner discovery.

        Caller revalidates evaluation membership and resource authority in this UoW.
        These three owner types share evaluation's current scoped read contract.
        """
        p = {
            **scope_params(scope),
            "kind": resource.resource_kind,
            "id": resource.resource_id,
            "version": resource.resource_version,
        }
        rows = (
            (
                await self.db_session.execute(
                    text("""
          SELECT p.owner_kind,p.owner_id,p.resource_version
          FROM resource_pins p
          WHERE p.scope_key=:scope AND p.resource_kind=:kind AND p.resource_id=:id
          AND p.resource_version=:version AND p.available
          AND ((p.owner_kind='dataset_version' AND EXISTS(SELECT 1 FROM evaluation_dataset_versions v WHERE v.scope_key=:scope AND v.id::text=p.owner_id))
            OR (p.owner_kind='config_version' AND EXISTS(SELECT 1 FROM evaluation_config_versions v WHERE v.scope_key=:scope AND v.id::text=p.owner_id))
            OR (p.owner_kind='recording_version' AND EXISTS(SELECT 1 FROM evaluation_recording_versions v WHERE v.scope_key=:scope AND v.id::text=p.owner_id)))
          ORDER BY p.owner_kind,p.owner_id LIMIT 100
        """),
                    p,
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]
