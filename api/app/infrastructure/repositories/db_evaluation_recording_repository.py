"""Recording facts in the caller transaction; no private Activity result reads."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound, ReplayMismatch
from app.domain.evaluation.recording import RecordingManifest, canonical
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


async def recording_version_owner(session, scope, owner_id):
    return bool(
        await session.scalar(
            text(
                "SELECT 1 FROM evaluation_recording_versions WHERE scope_key=:scope AND id=CAST(:id AS uuid)"
            ),
            params(scope, id=owner_id),
        )
    )


class DBEvaluationRecordingRepository:
    def __init__(self, db_session):
        self.db = db_session

    async def list_jobs(self, scope, *, after=None, limit=51):
        result = await self.db.execute(
            text(
                "SELECT id,source_run_id,status,revision,result_version,error,created_at FROM evaluation_recording_jobs WHERE scope_key=:scope AND NOT EXISTS(SELECT 1 FROM evaluation_resource_archives a WHERE a.scope_key=evaluation_recording_jobs.scope_key AND a.kind='recording' AND a.resource_id=evaluation_recording_jobs.id) AND (CAST(:after AS uuid) IS NULL OR id > :after) ORDER BY id LIMIT :limit"
            ),
            params(scope, after=after, limit=limit),
        )
        return [dict(row) for row in result.mappings().all()]

    async def job(self, scope, job_id, *, lock=False):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_recording_jobs WHERE scope_key=:scope AND id=:id"
                        + (" FOR UPDATE" if lock else "")
                    ),
                    params(scope, id=job_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DatasetNotFound("recording_unavailable")
        return dict(row)

    async def create(self, scope, job, selection, principal):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_recording_jobs(id,source_run_id,status,selection,principal,owner_user_id,team_id,created_by) VALUES(:id,:run,'queued',CAST(:selection AS jsonb),CAST(:principal AS jsonb),:owner,:team,:actor)"
            ),
            params(
                scope,
                id=job.id,
                run=job.source_run_id,
                selection=json.dumps(selection),
                principal=principal.model_dump_json(),
            ),
        )

    async def claim(self, scope, job_id, *, now=None):
        now = now or datetime.now(UTC)
        row = await self.job(scope, job_id, lock=True)
        if row["status"] in {"ready", "failed"} or (
            row["lease_until"] and row["lease_until"] > now
        ):
            return None
        token = uuid4()
        await self.db.execute(
            text(
                "UPDATE evaluation_recording_jobs SET status='running',claim_token=:token,lease_until=:lease WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=job_id, token=token, lease=now + timedelta(minutes=10)),
        )
        return token

    async def fail(self, scope, job_id, token, reason):
        await self.db.execute(
            text(
                "UPDATE evaluation_recording_jobs SET status='failed',error=:reason,lease_until=NULL WHERE scope_key=:scope AND id=:id AND claim_token=:token AND status='running'"
            ),
            params(scope, id=job_id, token=token, reason=reason),
        )

    async def fail_revoked_inventory(self, scope, selected):
        """Kernel-only lifecycle fence after current authorization denied this selected job."""
        if not await self.db.scalar(
            text("SELECT has_table_privilege(current_user,'evaluation_contract_captures','INSERT')")
        ):
            raise PermissionError("recording_lifecycle_kernel_only")
        result = await self.db.execute(
            text("""UPDATE evaluation_recording_jobs SET status='failed',error='recording_authority_revoked',lease_until=NULL
            WHERE scope_key=:scope AND id=:id AND revision=:revision AND status=:status
              AND claim_token IS NOT DISTINCT FROM CAST(:token AS uuid)
              AND lease_until IS NOT DISTINCT FROM CAST(:lease AS timestamptz)
              AND status IN ('queued','running') AND (lease_until IS NULL OR lease_until<CURRENT_TIMESTAMP)"""),
            params(
                scope,
                id=selected["id"],
                revision=selected["revision"],
                status=selected["status"],
                token=selected["claim_token"],
                lease=selected["lease_until"],
            ),
        )
        return result.rowcount == 1

    async def version(self, scope, version_id):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT body,digest FROM evaluation_recording_versions WHERE scope_key=:scope AND id=:id"
                    ),
                    params(scope, id=version_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None or hashlib.sha256(canonical(row["body"])).hexdigest() != row["digest"]:
            raise ReplayMismatch("recording_unavailable")
        return RecordingManifest.model_validate(row["body"])

    async def publish(self, scope, manifest, token):
        row = await self.job(scope, manifest.job_id, lock=True)
        if (
            row["status"] != "running"
            or row["claim_token"] != token
            or row["lease_until"] <= datetime.now(UTC)
        ):
            raise DatasetConflict("recording_claim_lost")
        body = manifest.model_dump(mode="json")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_recording_versions(id,job_id,body,digest,owner_user_id,team_id,created_by) VALUES(:id,:job,CAST(:body AS jsonb),:digest,:owner,:team,:actor)"
            ),
            params(
                scope,
                id=manifest.id,
                job=manifest.job_id,
                body=json.dumps(body),
                digest=hashlib.sha256(canonical(body)).hexdigest(),
            ),
        )
        for slot in manifest.slots:
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_recording_slots(id,version_id,match_key,object_id,body,owner_user_id,team_id,created_by) VALUES(:id,:version,:key,:object,CAST(:body AS jsonb),:owner,:team,:actor)"
                ),
                params(
                    scope,
                    id=slot.id,
                    version=manifest.id,
                    key=slot.match_key,
                    object=slot.object_id,
                    body=slot.model_dump_json(),
                ),
            )
        await self.db.execute(
            text(
                "UPDATE evaluation_recording_jobs SET status='ready',result_version=:version,lease_until=NULL WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=manifest.job_id, version=manifest.id),
        )

    async def capture(self, scope, run_id, activity_id, body):
        digest = hashlib.sha256(canonical(body)).hexdigest()
        await self.db.execute(
            text(
                "INSERT INTO evaluation_contract_captures(run_id,activity_id,body,digest,owner_user_id,team_id,created_by) VALUES(:run,:activity,CAST(:body AS jsonb),:digest,:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            params(scope, run=run_id, activity=activity_id, body=json.dumps(body), digest=digest),
        )

    async def captured(self, scope, run_id, activity_id):
        rows = (
            (
                await self.db.execute(
                    text(
                        "SELECT body,digest FROM evaluation_contract_captures WHERE scope_key=:scope AND run_id=:run AND activity_id=:activity"
                    ),
                    params(scope, run=run_id, activity=activity_id),
                )
            )
            .mappings()
            .all()
        )
        if len(rows) != 1:
            raise ReplayMismatch("contract_unavailable")
        row = rows[0]
        if (
            row["body"].get("unavailable")
            or hashlib.sha256(canonical(row["body"])).hexdigest() != row["digest"]
        ):
            raise ReplayMismatch("contract_unavailable")
        return row["body"]

    async def binding(self, scope, run_id):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT version_id,principal,admission FROM evaluation_replay_bindings WHERE scope_key=:scope AND run_id=:run"
                    ),
                    params(scope, run=run_id),
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def bind(self, scope, run_id, version_id, principal, *, admission=None):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_replay_bindings(run_id,version_id,principal,admission,owner_user_id,team_id,created_by) VALUES(:run,:version,CAST(:principal AS jsonb),CAST(:admission AS jsonb),:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            params(
                scope,
                run=run_id,
                version=version_id,
                principal=principal.model_dump_json(),
                admission=json.dumps(admission or {}),
            ),
        )
        row = await self.binding(scope, run_id)
        if row != {
            "version_id": version_id,
            "principal": principal.model_dump(mode="json"),
            "admission": admission or {},
        }:
            raise ReplayMismatch("replay_binding_changed")

    async def lock_call(self, scope, run_id):
        # Serialize exact slot claims within one run; different repetitions remain independent.
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"replay:{params(scope)['scope']}:{run_id}"},
        )

    async def consumed(self, scope, run_id, activity_id):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT slot_id,match_key,version_id FROM evaluation_replay_ledger WHERE scope_key=:scope AND run_id=:run AND activity_id=:activity"
                    ),
                    params(scope, run=run_id, activity=activity_id),
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def consume(self, scope, run_id, activity_id, version_id, slot):
        taken = await self.db.scalar(
            text(
                "SELECT activity_id FROM evaluation_replay_ledger WHERE scope_key=:scope AND run_id=:run AND version_id=:version AND slot_id=:slot"
            ),
            params(scope, run=run_id, version=version_id, slot=slot.id),
        )
        if taken is not None and taken != activity_id:
            raise ReplayMismatch("slot_already_consumed")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_replay_ledger(run_id,activity_id,version_id,slot_id,match_key,owner_user_id,team_id,created_by) VALUES(:run,:activity,:version,:slot,:key,:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            params(
                scope,
                run=run_id,
                activity=activity_id,
                version=version_id,
                slot=slot.id,
                key=slot.match_key,
            ),
        )

    async def object(self, scope, object_id):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT storage_key,digest,size_bytes FROM evaluation_recording_objects WHERE scope_key=:scope AND id=:id AND cleaned_at IS NULL"
                    ),
                    params(scope, id=object_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ReplayMismatch("recorded_object_unavailable")
        return dict(row)

    async def coverage(self, scope, run_id):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM evaluation_recording_slots s WHERE s.scope_key=b.scope_key AND s.version_id=b.version_id) AS total,(SELECT count(*) FROM evaluation_replay_ledger l WHERE l.scope_key=b.scope_key AND l.run_id=b.run_id) AS consumed,(SELECT count(*) FROM execution_activity_projection m WHERE m.run_id=b.run_id AND m.failure_code='REPLAY_MISMATCH' AND m.owner_user_id IS NOT DISTINCT FROM b.owner_user_id AND m.team_id IS NOT DISTINCT FROM b.team_id) AS mismatches FROM evaluation_replay_bindings b WHERE scope_key=:scope AND run_id=:run"
                    ),
                    params(scope, run=run_id),
                )
            )
            .mappings()
            .one()
        )

    async def approved(self, scope, run_id, activity_id):
        return bool(
            await self.db.scalar(
                text(
                    "SELECT 1 FROM execution_approval_projection WHERE run_id=:run AND subject_activity_id=:activity AND status='approved' AND ((CAST(:team AS text) IS NOT NULL AND team_id=:team) OR (CAST(:team AS text) IS NULL AND owner_user_id=:owner))"
                ),
                params(scope, run=run_id, activity=activity_id),
            )
        )

    async def connector_binding(self, scope, pack, connector_id, *, lock=False):
        if pack not in {"mcp", "a2a"}:
            raise ValueError("invalid_connector_kind")
        row = await self.db.scalar(
            text(
                f"SELECT to_jsonb(c) FROM {pack}_servers c WHERE id=:id AND enabled AND (visibility='global' OR (CAST(:team AS text) IS NOT NULL AND team_id=:team) OR (CAST(:team AS text) IS NULL AND owner_user_id=:owner AND team_id IS NULL))"
                + (" FOR SHARE" if lock else "")
            ),
            params(scope, id=connector_id),
        )
        if row is None:
            raise ReplayMismatch("connector_unavailable")
        # Ciphertext/configuration bytes never leave this metadata boundary.
        return hashlib.sha256(canonical(row)).hexdigest()

    async def content_identity(self, scope, run_id, content_id):
        digest = await self.db.scalar(
            text(
                "SELECT c.content_digest FROM execution_public_content c JOIN execution_content_bindings b USING(content_id) WHERE c.scope_key=:scope AND b.scope_key=:scope AND c.content_id=:id AND b.run_id=:run"
            ),
            params(scope, id=content_id, run=run_id),
        )
        if digest is None:
            raise ReplayMismatch("source_content_unavailable")
        return digest

    async def require_unadmitted(self, scope, run_id):
        if await self.db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM execution_stream_owners WHERE stream_type='run' AND stream_id=:run) OR EXISTS(SELECT 1 FROM execution_command_inbox WHERE stream_type='run' AND stream_id=:run)"
            ),
            {"run": str(run_id)},
        ):
            raise ReplayMismatch("replay_binding_after_admission")
