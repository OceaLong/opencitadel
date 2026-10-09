"""Evaluation SQL uses the caller transaction; only upload intents commit separately."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.domain.evaluation.errors import (
    CaseResourceUnavailable,
    DatasetConflict,
    DatasetNotFound,
    DatasetUnavailable,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
from app.infrastructure.execution.original_evidence import retain_read
from app.infrastructure.repositories.db_file_repository import DBFileRepository
from app.infrastructure.repositories.db_resource_pin_repository import (
    DBResourcePinRepository,
    scope_params,
)
from app.infrastructure.security.db_authorization import configure_session_authorization

logger = logging.getLogger(__name__)


def params(scope, **values):
    return {**scope_params(scope), "actor": scope.user_id, **values}


async def dataset_version_owner(session, scope, owner_id):
    return bool(
        await session.scalar(
            text(
                "SELECT 1 FROM evaluation_dataset_versions WHERE scope_key=:scope AND id=CAST(:id AS uuid)"
            ),
            params(scope, id=owner_id),
        )
    )


def authorize_original_user(scope, principal, *, write, user):
    """Apply the live authorization predicate to an original user read."""
    if scope.user_id != principal.user_id or (write and principal.is_auditor):
        raise PermissionError("evaluation permission denied")
    if (
        not user
        or user["status"] != "active"
        or user["token_version"] != principal.token_version
        or user["global_role"] != principal.global_role
    ):
        raise PermissionError("evaluation principal revoked")


def authorize_original_team(scope, principal, role):
    if not role or role != principal.team_roles.get(scope.team_id):
        raise PermissionError("evaluation membership revoked")


class DBEvaluationDatasetRepository:
    def __init__(self, db_session):
        self.db = db_session

    async def authorize(self, scope, principal, *, write):
        if scope.user_id != principal.user_id or (write and principal.is_auditor):
            raise PermissionError("evaluation permission denied")
        result = await self.db.execute(
            text("SELECT status,token_version,global_role FROM users WHERE id=:id"),
            {"id": principal.user_id},
        )
        try:
            user = result.mappings().first()
            retain_read(
                self.db,
                "principal-source",
                "dataset.authorize",
                {"scope": scope, "principal": principal, "write": write},
                user,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)
        authorize_original_user(scope, principal, write=write, user=user)
        if scope.team_id:
            result = await self.db.execute(
                text("SELECT role FROM team_members WHERE team_id=:team AND user_id=:user"),
                {"team": scope.team_id, "user": principal.user_id},
            )
            try:
                role = result.scalar()
                retain_read(
                    self.db,
                    "principal-source",
                    "dataset.team_role",
                    {"scope": scope, "principal": principal},
                    role,
                    source_result=result,
                )
            finally:
                result.close()
                synchronous = getattr(self.db, "sync_session", self.db)
                forget_result = getattr(synchronous, "forget_result", None)
                if callable(forget_result):
                    forget_result(result)
            authorize_original_team(scope, principal, role)

    async def lock_request(self, scope, request_id):
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"evaluation-request:{params(scope)['scope']}:{request_id}"},
        )

    async def lock_object(self, object_id):
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "evaluation-object:" + str(object_id)},
        )

    async def receipt(self, scope, request_id, fingerprint):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT fingerprint,result FROM evaluation_mutations WHERE scope_key=:scope AND request_id=:request"
                    ),
                    params(scope, request=request_id),
                )
            )
            .mappings()
            .first()
        )
        if row and row["fingerprint"] != fingerprint:
            raise DatasetConflict("request_conflict")
        return row["result"] if row else None

    async def save_receipt(self, scope, request_id, fingerprint, result):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_mutations(owner_user_id,team_id,created_by,request_id,fingerprint,result) VALUES (:owner,:team,:actor,:request,:fingerprint,CAST(:result AS jsonb))"
            ),
            params(scope, request=request_id, fingerprint=fingerprint, result=json.dumps(result)),
        )

    async def create(self, scope, dataset_id, name):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_datasets(owner_user_id,team_id,created_by,id,name,revision) VALUES (:owner,:team,:actor,:id,:name,1)"
            ),
            params(scope, id=dataset_id, name=name),
        )

    async def _members(self, scope, dataset_id, version_id=None):
        source = "evaluation_version_cases" if version_id else "evaluation_draft_cases"
        predicate = "m.version_id=:version" if version_id else "m.dataset_id=:dataset"
        result = await self.db.execute(
            text(
                f"SELECT c.*,o.storage_key,o.digest,o.cleaned_at FROM {source} m JOIN evaluation_case_revisions c ON c.scope_key=m.scope_key AND c.id=m.case_revision_id JOIN evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id WHERE m.scope_key=:scope AND {predicate} ORDER BY m.case_key"
            ),
            params(scope, dataset=dataset_id, version=version_id),
        )
        try:
            output = []
            for row in result.mappings():
                retain_read(
                    self.db,
                    "version-source",
                    "dataset.members",
                    {"scope": scope, "dataset": dataset_id, "version": version_id},
                    row,
                    source_result=result,
                )
                output.append(dict(row))
            if not output:
                retain_read(
                    self.db,
                    "version-source",
                    "dataset.members",
                    {"scope": scope, "dataset": dataset_id, "version": version_id},
                    [],
                    source_result=result,
                )
            return output
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)

    async def get_draft(self, scope, dataset_id, *, lock=False):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT id,name,revision FROM evaluation_datasets WHERE scope_key=:scope AND id=:id"
                        + (" FOR UPDATE" if lock else "")
                    ),
                    params(scope, id=dataset_id),
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DatasetNotFound("dataset_unavailable")
        return {**row, "members": await self._members(scope, dataset_id)}

    async def list_drafts(self, scope):
        return [
            dict(row)
            for row in (
                await self.db.execute(
                    text(
                        "SELECT d.id,d.name,d.revision,(SELECT count(*) FROM evaluation_draft_cases c WHERE c.scope_key=d.scope_key AND c.dataset_id=d.id) AS case_count,(SELECT count(*) FROM evaluation_dataset_versions v WHERE v.scope_key=d.scope_key AND v.dataset_id=d.id) AS version_count FROM evaluation_datasets d WHERE d.scope_key=:scope AND NOT EXISTS(SELECT 1 FROM evaluation_resource_archives a WHERE a.scope_key=d.scope_key AND a.kind='dataset' AND a.resource_id=d.id) ORDER BY d.created_at DESC,d.id LIMIT 1000"
                    ),
                    params(scope),
                )
            ).mappings()
        ]

    async def case_members(self, scope, ids):
        rows = (
            await self.db.execute(
                text(
                    "SELECT c.*,o.storage_key,o.digest,o.cleaned_at FROM evaluation_case_revisions c JOIN evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id WHERE c.scope_key=:scope AND c.id=ANY(CAST(:ids AS uuid[]))"
                ),
                params(scope, ids=ids),
            )
        ).mappings()
        members = {str(row["id"]): dict(row) for row in rows}
        if set(members) != set(ids):
            raise DatasetUnavailable("case_revision_unavailable")
        return [members[identity] for identity in ids]

    async def list_versions(self, scope, dataset_id, *, before=None, limit=51):
        result = await self.db.execute(
            text(
                "SELECT v.id,v.dataset_id,v.revision,(SELECT count(*) FROM evaluation_version_cases c WHERE c.scope_key=v.scope_key AND c.version_id=v.id) AS case_count FROM evaluation_dataset_versions v WHERE v.scope_key=:scope AND v.dataset_id=:dataset AND NOT EXISTS(SELECT 1 FROM evaluation_resource_archives a WHERE a.scope_key=v.scope_key AND a.kind='dataset' AND a.resource_id=v.dataset_id) AND (CAST(:before AS integer) IS NULL OR v.revision < :before) ORDER BY v.revision DESC LIMIT :limit"
            ),
            params(scope, dataset=dataset_id, before=before, limit=limit),
        )
        return [dict(row) for row in result.mappings().all()]

    async def get_version(self, scope, version_id):
        result = await self.db.execute(
            text(
                "SELECT id,dataset_id,revision FROM evaluation_dataset_versions WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=version_id),
        )
        try:
            row = result.mappings().first()
            retain_read(
                self.db,
                "version-source",
                "dataset.get_version",
                {"scope": scope, "id": version_id},
                row,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)
        if not row:
            raise DatasetNotFound("version_unavailable")
        return {**row, "members": await self._members(scope, row["dataset_id"], version_id)}

    async def save_import(self, scope, record):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_imports(owner_user_id,team_id,created_by,id,dataset_id,object_id,input_digest,draft_revision,content_type,errors,diff,expires_at) VALUES (:owner,:team,:actor,:id,:dataset_id,:object_id,:input_digest,:draft_revision,:content_type,CAST(:errors AS jsonb),CAST(:diff AS jsonb),:expires_at)"
            ),
            params(
                scope,
                **{
                    **record,
                    "errors": json.dumps(record["errors"]),
                    "diff": json.dumps(record["diff"]),
                },
            ),
        )

    async def get_import(self, scope, import_id, *, lock=False):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT i.*,o.storage_key,o.digest,o.cleaned_at FROM evaluation_imports i LEFT JOIN evaluation_object_intents o ON o.scope_key=i.scope_key AND o.id=i.object_id WHERE i.scope_key=:scope AND i.id=:id"
                        + (" FOR UPDATE OF i" if lock else "")
                    ),
                    params(scope, id=import_id),
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DatasetUnavailable("import_unavailable")
        return dict(row)

    async def mark_applied(self, scope, import_id):
        await self.db.execute(
            text("UPDATE evaluation_imports SET applied=true WHERE scope_key=:scope AND id=:id"),
            params(scope, id=import_id),
        )

    async def replace_cases(self, scope, dataset_id, cases, *, expected_revision):
        result = await self.db.execute(
            text(
                "UPDATE evaluation_datasets SET revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE scope_key=:scope AND id=:dataset AND revision=:revision"
            ),
            params(scope, dataset=dataset_id, revision=expected_revision),
        )
        if result.rowcount != 1:
            raise DatasetConflict("revision_conflict")
        await self.db.execute(
            text(
                "DELETE FROM evaluation_draft_cases WHERE scope_key=:scope AND dataset_id=:dataset"
            ),
            params(scope, dataset=dataset_id),
        )
        for case in cases:
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_case_revisions(owner_user_id,team_id,created_by,id,dataset_id,case_key,revision,object_id,object_index) VALUES (:owner,:team,:actor,:id,:dataset,:case_key,:revision,:object_id,:object_index) ON CONFLICT(scope_key,id) DO NOTHING"
                ),
                params(
                    scope,
                    dataset=dataset_id,
                    **{
                        key: case[key]
                        for key in ("id", "case_key", "revision", "object_id", "object_index")
                    },
                ),
            )
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_draft_cases(owner_user_id,team_id,created_by,dataset_id,case_key,case_revision_id) VALUES (:owner,:team,:actor,:dataset,:key,:id)"
                ),
                params(scope, dataset=dataset_id, key=case["case_key"], id=case["id"]),
            )

    async def publish(self, scope, dataset_id, version_id, *, expected_revision):
        result = await self.db.execute(
            text(
                "UPDATE evaluation_datasets SET revision=revision+1 WHERE scope_key=:scope AND id=:id AND revision=:revision"
            ),
            params(scope, id=dataset_id, revision=expected_revision),
        )
        if result.rowcount != 1:
            raise DatasetConflict("revision_conflict")
        revision = await self.db.scalar(
            text(
                "SELECT COALESCE(max(revision),0)+1 FROM evaluation_dataset_versions WHERE scope_key=:scope AND dataset_id=:dataset"
            ),
            params(scope, dataset=dataset_id),
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_dataset_versions(owner_user_id,team_id,created_by,id,dataset_id,revision) VALUES (:owner,:team,:actor,:id,:dataset,:revision)"
            ),
            params(scope, id=version_id, dataset=dataset_id, revision=revision),
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_version_cases(owner_user_id,team_id,created_by,version_id,dataset_id,case_key,case_revision_id) SELECT owner_user_id,team_id,:actor,:version,dataset_id,case_key,case_revision_id FROM evaluation_draft_cases WHERE scope_key=:scope AND dataset_id=:dataset"
            ),
            params(scope, version=version_id, dataset=dataset_id),
        )
        return await self.get_version(scope, version_id)

    async def certify_analysis(self, scope, principal, version_id, resources):
        from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority

        row = await self.get_version(scope, version_id)
        members = sorted(
            [
                [str(m["id"]), str(m["object_id"]), m["object_index"], m["digest"]]
                for m in row["members"]
            ],
            key=lambda member: member[0],
        )
        signed = await DBCurrentAuthority(
            self.db,
            signing_secret=self.db.info.get("database_authorization_signing_secret"),
        ).signed(
            scope,
            principal,
            operation="certify_dataset",
            version_id=str(version_id),
            members=members,
            resources=[r.model_dump(mode="json") for r in resources],
        )
        await self.db.scalar(
            text("SELECT public.opencitadel_analysis_certify_dataset(:body,:signature)"), signed
        )

    async def resources(self, scope, cases):
        identities = {}
        fields = {}
        files = DBFileRepository(self.db)
        for case in cases:
            selected = list(case.resources)
            if case.source_content_id:
                source = (
                    (
                        await self.db.execute(
                            text(
                                "SELECT c.content_digest,c.citation_refs FROM execution_public_content c JOIN execution_content_bindings b USING(content_id) WHERE c.scope_key=:scope AND b.scope_key=:scope AND c.content_id=CAST(:id AS uuid) AND b.run_id=:run AND b.step_id=:step"
                            ),
                            params(
                                scope,
                                id=case.source_content_id,
                                run=case.source_run_id,
                                step=case.source_step_id,
                            ),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if source is None:
                    raise ResourceUnavailable("source_content_unavailable")
                if case.input_status == "edited":
                    # The authored input is independent of a redacted source
                    # body, but its fixed source citations remain dependencies.
                    from app.domain.services.content_source_authority import (
                        content_source_available,
                    )
                    from app.infrastructure.repositories.db_knowledge_base_repository import (
                        DBKnowledgeBaseRepository,
                    )

                    for citation in source["citation_refs"]:
                        if not await content_source_available(
                            scope,
                            citation,
                            file_repository=files,
                            knowledge_repository=DBKnowledgeBaseRepository(self.db),
                        ):
                            raise ResourceUnavailable("source_content_unavailable")
                        if citation["resource_kind"] == "file":
                            identity = ResourceIdentity(
                                resource_kind="file",
                                resource_id=citation["file_id"],
                                resource_version=citation["content_digest"].removeprefix("sha256:"),
                            )
                            fields[("file", identity.resource_id)] = "source_content"
                        elif citation["resource_kind"] == "knowledge_base":
                            identity = ResourceIdentity(
                                resource_kind="knowledge_base",
                                resource_id=citation["knowledge_base_id"],
                                resource_version=citation["version_id"],
                            )
                            fields[("knowledge_base", identity.resource_id)] = "source_content"
                        else:
                            raise ResourceUnavailable("source_content_unavailable")
                        selected.append(identity)
                else:
                    selected.append(
                        ResourceIdentity(
                            resource_kind="execution_content",
                            resource_id=case.source_content_id,
                            resource_version=source["content_digest"],
                        )
                    )
            for index, attachment in enumerate(case.attachments):
                file = await files.get_by_id(attachment, scope=scope)
                if (
                    not file
                    or not file.content_available
                    or not file.content_digest
                    or not file.object_identity
                ):
                    raise CaseResourceUnavailable(f"attachments.{index}")
                selected.append(
                    ResourceIdentity(
                        resource_kind="file",
                        resource_id=file.id,
                        resource_version=file.content_digest.removeprefix("sha256:"),
                    )
                )
            selected.extend(
                ResourceIdentity(
                    resource_kind="knowledge_base",
                    resource_id=binding.resource_id,
                    resource_version=binding.version_id,
                )
                for binding in case.knowledge_bindings
            )
            for index, attachment in enumerate(case.attachments):
                fields[("file", attachment)] = f"attachments.{index}"
            for index, binding in enumerate(case.knowledge_bindings):
                fields[("knowledge_base", binding.resource_id)] = (
                    f"knowledge_bindings.{index}.version_id"
                )
            for resource in selected:
                identities[
                    (resource.resource_kind, resource.resource_id, resource.resource_version)
                ] = resource
        ordered = tuple(identities[key] for key in sorted(identities))
        for resource in ordered:
            try:
                await DBResourcePinRepository(self.db).resolve(scope, resource, lock=True)
            except ResourceUnavailable as error:
                raise CaseResourceUnavailable(
                    fields.get((resource.resource_kind, resource.resource_id), "resources")
                ) from error
        return ordered


class DatasetObjectLifecycle:
    """Independent intent commits use U07's dedicated bounded authorized pool."""

    def __init__(self, session_factory, objects, *, signing_secret):
        self.factory, self.objects, self.secret = session_factory, objects, signing_secret

    async def register(self, authorization, *, dataset_id, object_id, digest):
        if authorization.principal is None or authorization.scope is None:
            raise PermissionError("dataset upload requires explicit principal")
        scope = authorization.scope
        key = "evaluation/objects/" + str(object_id)
        async with asyncio.timeout(10), self.factory() as db:
            await configure_session_authorization(db, authorization, signing_secret=self.secret)
            repo = DBEvaluationDatasetRepository(db)
            await repo.authorize(scope, authorization.principal, write=True)
            await repo.get_draft(scope, dataset_id)
            await db.execute(
                text(
                    "INSERT INTO evaluation_object_intents(owner_user_id,team_id,created_by,id,dataset_id,storage_key,digest) VALUES (:owner,:team,:actor,:id,:dataset,:key,:digest)"
                ),
                params(scope, id=object_id, dataset=dataset_id, key=key, digest=digest),
            )
            await db.commit()
        return key

    async def cleanup(self, *, limit=100, now=None):
        """Kernel authority only; each attempt rotates and commits independently."""
        authorization = AuthorizationContext.system("evaluation-object-cleanup")
        now = now or datetime.now(UTC)
        async with asyncio.timeout(30), self.factory() as db:
            await configure_session_authorization(db, authorization, signing_secret=self.secret)
            rows = (
                await db.execute(
                    text(
                        "SELECT id,scope_key FROM evaluation_object_intents o WHERE cleaned_at IS NULL AND created_at<:before AND NOT EXISTS (SELECT 1 FROM evaluation_case_revisions c WHERE c.scope_key=o.scope_key AND c.object_id=o.id) AND NOT EXISTS (SELECT 1 FROM evaluation_imports i WHERE i.scope_key=o.scope_key AND i.object_id=o.id AND i.expires_at>:now) ORDER BY updated_at LIMIT :limit"
                    ),
                    {"limit": min(limit, 100), "before": now - timedelta(hours=1), "now": now},
                )
            ).all()
            await db.rollback()
            cleaned = 0
            for object_id, scope_key in rows:
                await configure_session_authorization(db, authorization, signing_secret=self.secret)
                locked = await db.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": "evaluation-object:" + str(object_id)},
                )
                if not locked:
                    await db.rollback()
                    continue
                row = (
                    await db.execute(
                        text(
                            "SELECT storage_key FROM evaluation_object_intents o WHERE scope_key=:scope AND id=:id AND cleaned_at IS NULL AND NOT EXISTS (SELECT 1 FROM evaluation_case_revisions c WHERE c.scope_key=o.scope_key AND c.object_id=o.id) AND NOT EXISTS (SELECT 1 FROM evaluation_imports i WHERE i.scope_key=o.scope_key AND i.object_id=o.id AND i.expires_at>:now) FOR UPDATE"
                        ),
                        {"scope": scope_key, "id": object_id, "now": now},
                    )
                ).first()
                if row:
                    deleted = False
                    try:
                        async with asyncio.timeout(10):
                            await self.objects.delete_bytes(row.storage_key)
                        deleted = True
                    except Exception:  # noqa: BLE001 - provider-independent cleanup boundary
                        logger.warning("Evaluation object cleanup deferred object_id=%s", object_id)
                    await db.execute(
                        text(
                            "UPDATE evaluation_object_intents SET updated_at=CURRENT_TIMESTAMP,cleaned_at=:cleaned WHERE scope_key=:scope AND id=:id"
                        ),
                        {
                            "scope": scope_key,
                            "id": object_id,
                            "cleaned": datetime.now(UTC) if deleted else None,
                        },
                    )
                    cleaned += int(deleted)
                await db.commit()
            return cleaned
