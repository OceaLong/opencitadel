"""Caller-transaction persistence and scoped metadata reads without secret decryption."""

import json

from sqlalchemy import text

from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
from app.domain.models.inference import ChatModelSettings, InferenceCapabilities, InferenceProvider
from app.infrastructure.execution.original_evidence import retain_read
from app.infrastructure.repositories.db_evaluation_dataset_repository import params

KINDS = frozenset({"config", "rubric", "suite"})


def table(kind):
    if kind not in KINDS:
        raise ValueError("invalid_configuration_kind")
    return "evaluation_" + kind + "_versions"


async def config_version_owner(session, scope, owner_id):
    return bool(
        await session.scalar(
            text(
                "SELECT 1 FROM evaluation_config_versions WHERE scope_key=:scope AND id=CAST(:id AS uuid)"
            ),
            params(scope, id=owner_id),
        )
    )


class DBEvaluationConfigurationRepository:
    def __init__(self, db_session):
        self.db = db_session

    async def draft(self, scope, kind, entity_id, *, lock=False):
        table(kind)
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT id,kind,name,revision,definition FROM evaluation_configuration_drafts WHERE scope_key=:scope AND id=:id AND kind=:kind AND NOT deleted"
                        + (" FOR UPDATE" if lock else "")
                    ),
                    params(scope, id=entity_id, kind=kind),
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DatasetNotFound("configuration_unavailable")
        return dict(row)

    async def create(self, scope, kind, entity_id, name, definition):
        table(kind)
        await self.db.execute(
            text(
                "INSERT INTO evaluation_configuration_drafts(id,kind,name,revision,definition,owner_user_id,team_id,created_by) VALUES (:id,:kind,:name,1,CAST(:definition AS jsonb),:owner,:team,:actor)"
            ),
            params(scope, id=entity_id, kind=kind, name=name, definition=json.dumps(definition)),
        )

    async def update(self, scope, kind, entity_id, revision, name, definition, *, deleted=False):
        row = await self.draft(scope, kind, entity_id, lock=True)
        if row["revision"] != revision:
            raise DatasetConflict("revision_conflict")
        await self.db.execute(
            text(
                "UPDATE evaluation_configuration_drafts SET name=:name,definition=CAST(:definition AS jsonb),revision=revision+1,deleted=:deleted,updated_at=CURRENT_TIMESTAMP WHERE scope_key=:scope AND id=:id"
            ),
            params(
                scope, id=entity_id, name=name, definition=json.dumps(definition), deleted=deleted
            ),
        )

    async def get_version(self, scope, kind, version_id):
        result = await self.db.execute(
            text(f"SELECT body FROM {table(kind)} WHERE scope_key=:scope AND id=:id"),
            params(scope, id=version_id),
        )
        try:
            body = result.scalar()
            retain_read(
                self.db,
                "version-source",
                "configuration.get_version",
                {"scope": scope, "kind": kind, "id": version_id},
                body,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)
        if body is None:
            raise DatasetNotFound("configuration_version_unavailable")
        return body

    async def publish(self, scope, kind, version):
        if await self.db.scalar(
            text(
                f"SELECT 1 FROM {table(kind)} WHERE scope_key=:scope AND entity_id=:entity AND revision=:revision"
            ),
            params(scope, entity=version.entity_id, revision=version.revision),
        ):
            raise DatasetConflict("version_already_published")
        body = version.model_dump(mode="json")
        columns, values, extra = "", "", {}
        if kind == "rubric":
            columns, values = ",judge_config_version", ",:judge"
            extra["judge"] = version.judge_config_version
        elif kind == "suite":
            columns, values = ",dataset_version,rubric_version", ",:dataset,:rubric"
            extra = {"dataset": version.dataset_version, "rubric": version.rubric_version}
        await self.db.execute(
            text(
                f"INSERT INTO {table(kind)}(id,entity_id,revision,name,body,fingerprint,owner_user_id,team_id,created_by{columns}) VALUES (:id,:entity,:revision,:name,CAST(:body AS jsonb),:fingerprint,:owner,:team,:actor{values})"
            ),
            params(
                scope,
                id=version.id,
                entity=version.entity_id,
                revision=version.revision,
                name=version.name,
                body=json.dumps(body),
                fingerprint=version.fingerprint,
                **extra,
            ),
        )
        if kind == "suite":
            for config_id in version.config_versions:
                await self.db.execute(
                    text(
                        "INSERT INTO evaluation_suite_configs(suite_version,config_version,owner_user_id,team_id,created_by) VALUES (:suite,:config,:owner,:team,:actor)"
                    ),
                    params(scope, suite=version.id, config=config_id),
                )

    async def list(self, scope, kind, *, versions=False, after=None, limit=50, entity_id=None):
        table(kind)
        source = table(kind) if versions else "evaluation_configuration_drafts"
        extra = "" if versions else " AND kind=:kind AND NOT deleted"
        identity = "entity_id" if versions else "id"
        predicate = f" AND NOT EXISTS(SELECT 1 FROM evaluation_resource_archives a WHERE a.scope_key={source}.scope_key AND a.kind=:kind AND a.resource_id={source}.{identity})"
        predicate += " AND id > CAST(:after AS uuid)" if after else ""
        if entity_id and versions:
            predicate += " AND entity_id=:entity"
        return [
            dict(r)
            for r in (
                await self.db.execute(
                    text(
                        f"SELECT id,name,revision FROM {source} WHERE scope_key=:scope{extra}{predicate} ORDER BY id LIMIT :limit"
                    ),
                    params(scope, kind=kind, after=after, limit=limit, entity=entity_id),
                )
            ).mappings()
        ]

    async def metadata(self, scope, selection):
        # RLS plus explicit global/current-scope predicate on BOTH independently scoped rows.
        visible = (
            "(visibility='global' OR team_id=:team OR (team_id IS NULL AND owner_user_id=:owner))"
        )
        row = (
            (
                await self.db.execute(
                    text(
                        f"SELECT id,endpoint_id,model_name,kind,settings,capabilities,extra_params,input_price_per_million,output_price_per_million FROM inference_models WHERE id=:id AND {visible}"
                    ),
                    params(scope, id=selection.model_id),
                )
            )
            .mappings()
            .first()
        )
        if not row or row["kind"] != "chat":
            raise ValueError("model_unavailable")
        endpoint = (
            (
                await self.db.execute(
                    text(
                        f"SELECT id,provider,base_url,(length(credential)>0) AS credential_configured FROM inference_endpoints WHERE id=:id AND {visible}"
                    ),
                    params(scope, id=row["endpoint_id"]),
                )
            )
            .mappings()
            .first()
        )
        if not endpoint:
            raise ValueError("endpoint_unavailable")
        # Store only locator; endpoint URL can contain secrets and never leaves this reader.
        from app.domain.evaluation.configuration import digest

        return {
            "identity": {
                "model_id": row["id"],
                "configured_model": row["model_name"],
                "provider": InferenceProvider(endpoint["provider"]).value,
                "endpoint_id": endpoint["id"],
                "endpoint_digest": digest(endpoint["base_url"]),
            },
            "settings": ChatModelSettings.model_validate(row["settings"]).model_dump(mode="json"),
            "capabilities": InferenceCapabilities.model_validate(row["capabilities"]).model_dump(
                mode="json"
            ),
            "extra_params_present": bool(row["extra_params"]),
            "credential_ref": {"kind": "inference_endpoint", "id": endpoint["id"]},
            "credential_configured": endpoint["credential_configured"],
            "price": {
                "input": row["input_price_per_million"] or None,
                "output": row["output_price_per_million"] or None,
                "coverage": "unknown",
            },
        }

    async def candidate_metadata(self, scope):
        """Same scoped model/endpoint set as runtime, without decrypting credentials."""
        from app.domain.evaluation.configuration import ConfigSelection

        predicates = [
            f"({alias}.visibility='global' OR {alias}.team_id=:team OR "
            f"({alias}.team_id IS NULL AND {alias}.owner_user_id=:owner))"
            for alias in ("m", "e")
        ]
        identities = (
            (
                await self.db.execute(
                    text(
                        "SELECT m.id FROM inference_models m JOIN inference_endpoints e ON e.id=m.endpoint_id "
                        "WHERE m.kind='chat' AND "
                        + " AND ".join(predicates)
                        + " ORDER BY m.created_at,m.id"
                    ),
                    params(scope),
                )
            )
            .scalars()
            .all()
        )
        return [
            await self.metadata(scope, ConfigSelection(model_id=identity))
            for identity in identities
        ]

    async def save_preflight(self, scope, result):
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"evaluation-preflight:{params(scope)['scope']}:{result.suite_version}"},
        )
        revision = 1 + (
            await self.db.scalar(
                text(
                    "SELECT COALESCE(max(revision),0) FROM evaluation_preflights WHERE scope_key=:scope AND suite_version=:suite"
                ),
                params(scope, suite=result.suite_version),
            )
        )
        result = result.model_copy(update={"revision": revision})
        await self.db.execute(
            text(
                "INSERT INTO evaluation_preflights(id,suite_version,revision,body,owner_user_id,team_id,created_by) VALUES (:id,:suite,:revision,CAST(:body AS jsonb),:owner,:team,:actor)"
            ),
            params(
                scope,
                id=result.id,
                suite=result.suite_version,
                revision=revision,
                body=result.model_dump_json(),
            ),
        )
        return result
