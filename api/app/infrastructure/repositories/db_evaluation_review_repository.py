"""Scoped reads and one signed typed command; no API grants to kernel score tables."""

import hashlib
import hmac
import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


class DBEvaluationReviewRepository:
    def __init__(self, work, *, signing_secret):
        self.work, self.db, self.secret = work, work.db_session, signing_secret

    async def result(self, scope, result_id):
        row = (
            (
                await self.db.execute(
                    text("""SELECT r.*,a.run_id,a.run_revision,b.suite_version,(SELECT rs.rubric_id FROM evaluation_review_states rs WHERE rs.scope_key=r.scope_key AND rs.result_id=r.id ORDER BY rs.evaluation_revision DESC LIMIT 1) AS current_rubric
          FROM evaluation_batch_results r JOIN evaluation_batches b ON b.scope_key=r.scope_key AND b.id=r.batch_id
          JOIN evaluation_batch_attempts a ON a.scope_key=r.scope_key AND a.result_id=r.id AND a.attempt=r.attempt
          WHERE r.scope_key=:scope AND r.id=:id"""),
                    params(scope, id=result_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DatasetNotFound("review_not_found")
        return dict(row)

    async def current_context(self, scope, result_id):
        # One statement captures mutable current rubric, both revisions and human heads.
        row = (
            (
                await self.db.execute(
                    text("""
        WITH target AS (
          SELECT r.id AS result_id,r.batch_id,r.revision AS result_revision,
            COALESCE(h.revision,0) AS evaluation_revision,
            COALESCE((SELECT rs.rubric_id FROM evaluation_review_states rs
              WHERE rs.scope_key=r.scope_key AND rs.result_id=r.id
              ORDER BY rs.evaluation_revision DESC LIMIT 1), (sv.body->>'rubric_version')::uuid) AS rubric_id
          FROM evaluation_batch_results r
          JOIN evaluation_batches b ON b.scope_key=r.scope_key AND b.id=r.batch_id
          JOIN evaluation_suite_versions sv ON sv.scope_key=r.scope_key AND sv.id=b.suite_version
          LEFT JOIN evaluation_score_heads h ON h.scope_key=r.scope_key AND h.batch_id=r.batch_id
          WHERE r.scope_key=:scope AND r.id=:id
        ) SELECT t.*, rv.body AS rubric,
          COALESCE((SELECT jsonb_agg(to_jsonb(head)) FROM (
            SELECT DISTINCT ON (s.dimension) s.id,s.dimension,s.value,s.status,s.reason,s.evidence,s.supersedes_id,v.evaluation_revision
            FROM evaluation_scores s JOIN evaluation_score_sets v ON v.scope_key=s.scope_key AND v.id=s.set_id
            WHERE v.scope_key=:scope AND v.result_id=t.result_id AND v.rubric_revision=t.rubric_id AND v.source='human'
            AND v.evaluation_revision<=t.evaluation_revision
            ORDER BY s.dimension,v.evaluation_revision DESC
          ) head),'[]'::jsonb) AS human_heads
        FROM target t JOIN evaluation_rubric_versions rv ON rv.scope_key=:scope AND rv.id=t.rubric_id
        """),
                    params(scope, id=result_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DatasetNotFound("review_not_found")
        return dict(row)

    async def rows(self, scope, *, after=None, limit=201):
        return [
            dict(r)
            for r in (
                await self.db.execute(
                    text("""SELECT r.id FROM evaluation_batch_results r
          WHERE r.scope_key=:scope AND r.execution_status='succeeded'
          AND (CAST(:after AS uuid) IS NULL OR r.id>CAST(:after AS uuid)) ORDER BY r.id LIMIT :limit"""),
                    params(scope, after=after, limit=limit),
                )
            ).mappings()
        ]

    async def prior(self, scope, result_id, kind, request_id, fingerprint):
        row = (
            (
                await self.db.execute(
                    text(
                        """SELECT fingerprint,receipt,created_by FROM evaluation_review_commands WHERE scope_key=:scope AND result_id=:result AND kind=:kind AND request_id=:request"""
                    ),
                    params(scope, result=result_id, kind=kind, request=request_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row:
            if row["fingerprint"] != fingerprint or row["created_by"] != scope.user_id:
                raise DatasetConflict("review_request_conflict")
            return row["receipt"]
        return None

    async def submit(self, payload, *, authorization):
        from uuid import UUID

        from app.domain.models.audit_log import AuditLog

        scope, principal = authorization.scope, authorization.principal
        if (
            scope is None
            or principal is None
            or payload["principal"] != principal.model_dump(mode="json")
        ):
            raise PermissionError("review_authorization_denied")
        result_id = UUID(payload["result_id"])
        kind, request_id = payload["kind"], payload["request_id"]
        async with self.db.begin_nested():
            await self.work.evaluation_dataset.authorize(scope, principal, write=True)
            await self.work.evaluation_dataset.lock_request(
                scope, "review:" + str(result_id) + ":" + kind + ":" + request_id
            )
            prior = await self.prior(scope, result_id, kind, request_id, payload["fingerprint"])
            if prior is not None:
                return prior
            result = await self.command(payload)
            await self.work.audit.add_review(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.review." + kind,
                    resource_type="evaluation_result",
                    resource_id=str(result_id),
                    request_id=request_id,
                    metadata={
                        "command_id": result["id"],
                        "evaluation_revision": result["evaluation_revision"],
                        "result_revision": result["result_revision"],
                    },
                ),
                authorization=authorization,
            )
            return result

    async def command(self, payload):
        from uuid import UUID

        from app.domain.models.authorization import AuthorizationContext
        from app.domain.models.scope import OwnerScope, Principal
        from app.infrastructure.repositories.db_physical_requester_repository import (
            DBPhysicalRequesterRepository,
        )

        payload = dict(payload)
        if payload["kind"] != "cancel":
            principal = Principal.model_validate(payload["principal"])
            scope = (
                OwnerScope.team(principal.user_id, payload["scope"][5:])
                if payload["scope"].startswith("team:")
                else OwnerScope.personal(principal.user_id)
            )
            seal = await DBPhysicalRequesterRepository(self.db, signing_secret=self.secret).capture(
                scope,
                AuthorizationContext.for_principal(principal, scope=scope),
                run_id=UUID(payload["candidate"]["run_id"]),
            )
            payload["effect_proof"] = json.dumps(
                seal["proof"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            payload["effect_signature"] = seal["signature"]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            self.secret.encode(), ("opencitadel:e09:command:v1:" + encoded).encode(), hashlib.sha256
        ).hexdigest()
        try:
            return await self.db.scalar(
                text("SELECT public.opencitadel_e09_command(:encoded,:signature)"),
                {"encoded": encoded, "signature": signature},
            )
        except DBAPIError as error:
            message = str(error.orig)
            if "review_revision_conflict" in message:
                raise DatasetConflict("review_revision_conflict") from error
            if "review_not_found" in message or "review_rubric_unavailable" in message:
                raise DatasetNotFound("review_not_found") from error
            if "review_source_unavailable" in message:
                from app.domain.models.resource_pin import ResourceUnavailable

                raise ResourceUnavailable("review_source_unavailable") from error
            if "review_authorization" in message:
                raise PermissionError("review_authorization_denied") from error
            if "review_" in message:
                raise ValueError("review_command_invalid") from error
            raise

    async def get(self, scope, command_id):
        result = await self.db.scalar(
            text(
                "SELECT receipt FROM evaluation_review_commands WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=command_id),
        )
        if result is None:
            raise DatasetNotFound("review_not_found")
        return result

    async def claim(self, *, limit=100):
        return [
            dict(row)
            for row in (
                await self.db.execute(
                    text("""WITH ready AS (
          SELECT scope_key,id FROM evaluation_review_commands WHERE status='queued' OR (status='processing' AND claim_until<clock_timestamp())
          ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT :limit)
          UPDATE evaluation_review_commands c SET status='processing',generation=c.generation+1,claim_until=clock_timestamp()+interval '5 minutes',
            receipt=jsonb_set(c.receipt,'{status}','"processing"') FROM ready
          WHERE c.scope_key=ready.scope_key AND c.id=ready.id RETURNING c.*"""),
                    {"limit": limit},
                )
            ).mappings()
        ]

    async def finish(self, scope, command, *, status, judge_run_id=None, error=None):
        return await self.db.scalar(
            text("""UPDATE evaluation_review_commands SET status=CAST(:status AS text),judge_run_id=CAST(:run AS uuid),error=CAST(:error AS text),claim_until=NULL,
          receipt=receipt || jsonb_build_object('status',CAST(:status AS text),'judge_run_id',CAST(:run AS uuid),'error',CAST(:error AS text))
          WHERE scope_key=:scope AND id=:id AND generation=:generation AND status='processing' RETURNING id"""),
            params(
                scope,
                id=command["id"],
                generation=command["generation"],
                status=status,
                run=judge_run_id,
                error=error,
            ),
        )

    async def refresh_commands(self, *, limit=100):
        await self.db.execute(
            text("""WITH terminal AS (
          SELECT c.scope_key,c.id,w.status,w.error,w.evaluation_revision FROM evaluation_review_commands c
          JOIN evaluation_judge_intents i ON i.scope_key=c.scope_key AND i.run_id=c.judge_run_id
          JOIN evaluation_judge_work w ON w.scope_key=i.scope_key AND w.intent_id=i.id
          WHERE c.status IN ('submitted','cancelling') AND w.status IN ('settled','stopped')
          ORDER BY c.created_at,c.id LIMIT :limit
        ), mapped AS (SELECT *,CASE WHEN status='settled' THEN 'completed' WHEN error='judge_cancelled' THEN 'cancelled' ELSE 'failed' END AS public_status FROM terminal)
        UPDATE evaluation_review_commands c SET status=m.public_status,error=CASE WHEN m.public_status='failed' THEN 'review_execution_unavailable' END,
          receipt=c.receipt || jsonb_build_object('status',m.public_status,'error',CASE WHEN m.public_status='failed' THEN 'review_execution_unavailable' END,
          'evaluation_revision',COALESCE(m.evaluation_revision,(c.receipt->>'evaluation_revision')::bigint))
        FROM mapped m WHERE c.scope_key=m.scope_key AND c.id=m.id"""),
            {"limit": limit},
        )

    async def cancellation(self, scope, command_id, run_id, principal):
        return await self.db.scalar(
            text("""SELECT true FROM evaluation_review_cancellations x
          JOIN evaluation_review_commands c ON c.scope_key=x.scope_key AND c.id=x.command_id
          WHERE x.scope_key=:scope AND x.command_id=:command AND x.run_id=:run
          AND x.result_id=c.result_id AND x.principal=c.principal
          AND x.principal=CAST(:principal AS jsonb) AND c.kind='cancel'
          AND c.judge_run_id=x.run_id"""),
            params(
                scope,
                command=command_id,
                run=run_id,
                principal=json.dumps(principal.model_dump(mode="json")),
            ),
        )

    async def validate_cancellation(self, scope, command_id, run_id, principal):
        command = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_review_commands WHERE scope_key=:scope AND id=:id AND kind='cancel' AND judge_run_id=:run AND principal=CAST(:principal AS jsonb)"
                    ),
                    params(
                        scope,
                        id=command_id,
                        run=run_id,
                        principal=json.dumps(principal.model_dump(mode="json")),
                    ),
                )
            )
            .mappings()
            .one_or_none()
        )
        if command is None:
            raise ValueError("review_cancel_binding_mismatch")
        if await self.cancellation(scope, command_id, run_id, principal):
            return
        row = await self.result(scope, command["result_id"])
        if (
            row["revision"] != command["payload"]["expected_result_revision"]
            or await self.work.evaluation_score.revision(scope, command["batch_id"])
            != command["payload"]["expected_revision"]
        ):
            raise ValueError("review_revision_conflict")

    async def bind_cancellation(self, scope, command_id, run_id, principal):
        result = await self.db.execute(
            text("""INSERT INTO evaluation_review_cancellations(command_id,run_id,result_id,principal,owner_user_id,team_id,created_by)
          SELECT c.id,c.judge_run_id,c.result_id,c.principal,c.owner_user_id,c.team_id,c.created_by
          FROM evaluation_review_commands c JOIN evaluation_judge_intents i ON i.scope_key=c.scope_key AND i.run_id=c.judge_run_id AND i.result_id=c.result_id
          WHERE c.scope_key=:scope AND c.id=:command AND c.judge_run_id=:run AND c.kind='cancel'
          AND c.principal=CAST(:principal AS jsonb) ON CONFLICT DO NOTHING"""),
            params(
                scope,
                command=command_id,
                run=run_id,
                principal=json.dumps(principal.model_dump(mode="json")),
            ),
        )
        if not result.rowcount and not await self.cancellation(
            scope, command_id, run_id, principal
        ):
            raise ValueError("review_cancel_binding_mismatch")

    async def lock_pins(self, scope, owner_kind, owner_id):
        await self.db.execute(
            text(
                "SELECT id FROM resource_pins WHERE scope_key=:scope AND owner_kind=:kind AND owner_id=:id FOR SHARE"
            ),
            params(scope, kind=owner_kind, id=owner_id),
        )

    async def lock_config(self, scope, selection):
        # Existing API-owned configuration metadata; no private execution grants.
        await self.db.execute(
            text("SELECT id FROM inference_models WHERE id=:id FOR SHARE"),
            {"id": selection.model_id},
        )
        await self.db.execute(
            text(
                "SELECT e.id FROM inference_endpoints e JOIN inference_models m ON m.endpoint_id=e.id WHERE m.id=:id FOR SHARE OF e"
            ),
            {"id": selection.model_id},
        )
        if selection.skill_id:
            await self.db.execute(
                text("SELECT id FROM skills WHERE id=:id FOR SHARE"), {"id": selection.skill_id}
            )

    async def source_content(self, scope, run_id):
        from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable

        rows = (
            (
                await self.db.execute(
                    text("""SELECT DISTINCT c.content_id,c.content_digest,c.redacted,c.citation_refs
          FROM execution_public_content c JOIN execution_content_bindings b ON b.scope_key=c.scope_key AND b.content_id=c.content_id
          WHERE b.scope_key=:scope AND b.run_id=:run AND b.phase='output'"""),
                    params(scope, run=run_id),
                )
            )
            .mappings()
            .all()
        )
        if not rows or any(row["redacted"] for row in rows):
            raise ResourceUnavailable("review_source_unavailable")
        for row in rows:
            for citation in row["citation_refs"]:
                if citation.get("availability") != "available":
                    raise ResourceUnavailable("review_source_unavailable")
                resource = ResourceIdentity(
                    resource_kind="file"
                    if citation.get("resource_kind") == "file"
                    else "knowledge_base",
                    resource_id=citation["file_id"]
                    if citation.get("resource_kind") == "file"
                    else citation["knowledge_base_id"],
                    resource_version=citation["content_digest"]
                    if citation.get("resource_kind") == "file"
                    else citation["version_id"],
                )
                await self.work.resource_pins.resolve(scope, resource, lock=True)
            await self.work.resource_pins.resolve(
                scope,
                ResourceIdentity(
                    resource_kind="execution_content",
                    resource_id=str(row["content_id"]),
                    resource_version=row["content_digest"],
                ),
                lock=True,
            )
        return sorted(str(row["content_id"]) for row in rows)

    async def requirements(self, scope, batch_id, requirements=None):
        if requirements is not None:
            await self.db.execute(
                text("""INSERT INTO evaluation_review_requirements(batch_id,suite_id,rubric_id,requirements,owner_user_id,team_id,created_by)
            SELECT b.id,b.suite_version,sv.rubric_version,CAST(:requirements AS jsonb),b.owner_user_id,b.team_id,b.created_by
            FROM evaluation_batches b JOIN evaluation_suite_versions sv ON sv.scope_key=b.scope_key AND sv.id=b.suite_version
            WHERE b.scope_key=:scope AND b.id=:batch ON CONFLICT DO NOTHING"""),
                params(scope, batch=batch_id, requirements=json.dumps(requirements)),
            )
        return await self.db.scalar(
            text(
                "SELECT requirements FROM evaluation_review_requirements WHERE scope_key=:scope AND batch_id=:batch"
            ),
            params(scope, batch=batch_id),
        )

    async def model_rubric(self, scope, candidate, rubric_id, rubric, applicable, revision):
        """A settled bound model rubric becomes current without erasing older human streams."""
        if await self.requirements(scope, candidate.batch_id) is None:
            raise ValueError("review_requirements_unavailable")
        required = sorted(
            {c["dimension_id"] for c in rubric["required_conditions"] if c["source"] == "human"}
            & set(applicable)
        )
        received = (
            (
                await self.db.execute(
                    text("""SELECT DISTINCT ON(q.dimension) q.dimension,q.status FROM evaluation_scores q
          JOIN evaluation_score_sets s ON s.scope_key=q.scope_key AND s.id=q.set_id
          WHERE s.scope_key=:scope AND s.result_id=:result AND s.rubric_revision=:rubric AND s.source='human'
          ORDER BY q.dimension,s.evaluation_revision DESC"""),
                    params(scope, result=candidate.result_id, rubric=rubric_id),
                )
            )
            .mappings()
            .all()
        )
        valid = {r["dimension"] for r in received if r["status"] == "valid"}
        status = (
            "pending"
            if not set(required) <= valid
            else "complete"
            if required or received
            else "not_required"
        )
        await self.db.execute(
            text("""INSERT INTO evaluation_review_states(result_id,batch_id,rubric_id,status,required_dimensions,applicable_dimensions,evaluation_revision,owner_user_id,team_id,created_by)
          VALUES(:result,:batch,:rubric,:status,CAST(:required AS jsonb),CAST(:applicable AS jsonb),:revision,:owner,:team,:actor)
          ON CONFLICT(scope_key,result_id,rubric_id) DO UPDATE SET status=excluded.status,evaluation_revision=excluded.evaluation_revision"""),
            params(
                scope,
                result=candidate.result_id,
                batch=candidate.batch_id,
                rubric=rubric_id,
                status=status,
                required=json.dumps(required),
                applicable=json.dumps(list(applicable)),
                revision=revision,
            ),
        )
        await self.db.execute(
            text("""UPDATE evaluation_batches b SET review_status=CASE WHEN EXISTS(
          SELECT 1 FROM evaluation_batch_results r WHERE r.scope_key=b.scope_key AND r.batch_id=b.id AND COALESCE(
            (SELECT rs.status FROM evaluation_review_states rs WHERE rs.scope_key=r.scope_key AND rs.result_id=r.id ORDER BY rs.evaluation_revision DESC LIMIT 1),
            CASE WHEN COALESCE(((SELECT facts.requirements FROM evaluation_review_requirements facts WHERE facts.scope_key=b.scope_key AND facts.batch_id=b.id)->>r.case_revision_id::text)::boolean,false) THEN 'pending' ELSE 'not_required' END)='pending')
          THEN 'pending' WHEN EXISTS(SELECT 1 FROM evaluation_review_states rs WHERE rs.scope_key=b.scope_key AND rs.batch_id=b.id AND rs.status='complete') THEN 'complete' ELSE 'not_required' END
          WHERE b.scope_key=:scope AND b.id=:batch"""),
            params(scope, batch=candidate.batch_id),
        )
