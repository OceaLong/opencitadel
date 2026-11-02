"""Scoped immutable receipts in caller-owned transactions; no view dependencies."""

import json
from uuid import UUID, uuid5

from sqlalchemy import text

from app.application.execution import activity_types
from app.application.execution.view_facts import attempt_key
from app.domain.models.execution_usage import content_revision
from app.infrastructure.repositories.db_resource_pin_repository import scope_params


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class DBExecutionUsageRepository:
    def __init__(self, session, *, signing_secret=None):
        self.session, self.signing_secret = session, signing_secret

    async def tool_intent_refresh_target(self, scope, run_id, activity_id, subject_digest):
        from app.domain.errors import ConflictError, NotFoundError

        row = (
            (
                await self.session.execute(
                    text("""
            SELECT * FROM public.opencitadel_tool_intent_state(:scope,:run,:activity)
        """),
                    {**scope_params(scope), "run": str(run_id), "activity": activity_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("Tool intent not found")
        if row["subject_digest"] != subject_digest:
            raise ConflictError("Tool intent subject changed")
        if row["status"] != "failed" or row["failure_code"] not in {
            "TOOL_CONTRACT_CHANGED",
            "TOOL_AUTHORIZATION_CHANGED",
            "TOOL_INTENT_REFRESH_FAILED",
        }:
            raise ConflictError("Tool intent is not eligible for renewal")

    async def tool_intent_refresh_status(self, scope, run_id, activity_id, command_id):
        from app.domain.errors import NotFoundError

        row = (
            (
                await self.session.execute(
                    text("""
            SELECT status,rejection_code FROM execution_command_inbox
            WHERE command_id=:command AND command_type='RefreshToolIntent' AND stream_type='run'
              AND stream_id=:run AND payload->>'activity_id'=:activity
              AND ((CAST(:team AS text) IS NOT NULL AND team_id=:team)
                   OR (CAST(:team AS text) IS NULL AND owner_user_id=:owner AND team_id IS NULL))
        """),
                    {
                        **scope_params(scope),
                        "run": str(run_id),
                        "activity": str(activity_id),
                        "command": command_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("Refresh request not found")
        return {
            "command_id": command_id,
            "status": "rejected"
            if row["rejection_code"] or row["status"] == "dead_lettered"
            else "applied"
            if row["status"] == "accepted"
            else "pending",
        }

    async def approval_subject(self, scope, run_id, approval_id):
        row = (
            (
                await self.session.execute(
                    text("""
            SELECT * FROM public.opencitadel_approval_subject(:scope,:run,:approval)
        """),
                    {**scope_params(scope), "run": str(run_id), "approval": str(approval_id)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("approval is unavailable")
        return row["subject_digest"]

    async def invocation_authority(self, scope, run_id, *, lock=False):
        """Reuse signed admission provenance and the current principal predicate."""
        from app.domain.evaluation.configuration import digest
        from app.domain.models.scope import OwnerScope, Principal
        from app.infrastructure.repositories.db_physical_requester_repository import (
            DBPhysicalRequesterRepository,
        )

        admitted = (
            (
                await self.session.execute(
                    text(
                        "SELECT body FROM execution_configurations WHERE scope_key=:scope AND run_id=:run "
                        "AND body->>'stage'='admission' ORDER BY created_at,id LIMIT 1"
                    ),
                    {**scope_params(scope), "run": run_id},
                )
            )
            .mappings()
            .first()
        )
        requester = DBPhysicalRequesterRepository(self.session, signing_secret=self.signing_secret)
        if admitted is None or "physical_requester" not in admitted["body"]:
            raise PermissionError("invocation_requester_proof_required")
        try:
            proof = requester.verify(
                admitted["body"]["physical_requester"], scope=scope, run_id=run_id
            )
        except (ValueError, TypeError, KeyError) as error:
            raise PermissionError("invocation_requester_proof_invalid") from error
        if proof["kind"] == "user":
            principal = Principal.model_validate(proof["principal"])
            scope = (
                OwnerScope.team(principal.user_id, scope.team_id)
                if scope.team_id
                else OwnerScope.personal(principal.user_id)
            )
            await self.authorize_principal(scope, principal, lock=lock)
        elif proof["kind"] != "system" or not proof.get("actor"):
            raise PermissionError("invocation_requester_proof_invalid")
        return {"scope": scope.model_dump(mode="json"), "authorization_epoch": digest(proof)}

    async def authorize_principal(self, scope, principal, *, lock=False):
        from app.infrastructure.repositories.db_evaluation_dataset_repository import (
            DBEvaluationDatasetRepository,
        )

        if lock:
            await self.session.execute(
                text("SELECT id FROM users WHERE id=:id FOR SHARE"), {"id": principal.user_id}
            )
            if scope.team_id:
                await self.session.execute(
                    text(
                        "SELECT user_id FROM team_members WHERE team_id=:team AND user_id=:user FOR SHARE"
                    ),
                    {"team": scope.team_id, "user": principal.user_id},
                )
        await DBEvaluationDatasetRepository(self.session).authorize(scope, principal, write=True)

    async def authorize_tool(self, scope, *, skill_id, name, pack, connector_ids, lock=False):
        from app.domain.services.tools.tool_names import is_tool_allowed

        if skill_id is None:
            return
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT enabled,allowed_tools,mcp_server_refs,a2a_server_refs FROM skills WHERE id=:id "
                        "AND (visibility='global' OR (CAST(:team AS text) IS NOT NULL AND team_id=:team) "
                        "OR (CAST(:team AS text) IS NULL AND owner_user_id=:owner AND team_id IS NULL))"
                        + (" FOR SHARE" if lock else "")
                    ),
                    {**scope_params(scope), "id": skill_id},
                )
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or not row["enabled"]
            or (
                row["allowed_tools"] is not None and not is_tool_allowed(name, row["allowed_tools"])
            )
        ):
            raise PermissionError("tool_skill_authorization_changed")
        if pack in {"mcp", "a2a"} and not set(connector_ids).issubset(
            row[pack + "_server_refs"] or []
        ):
            raise PermissionError("tool_connector_authorization_changed")

    async def capture_requester(self, scope, authorization, *, run_id):
        from app.infrastructure.repositories.db_physical_requester_repository import (
            DBPhysicalRequesterRepository,
        )

        return await DBPhysicalRequesterRepository(
            self.session, signing_secret=self.signing_secret
        ).capture(scope, authorization, run_id=run_id)

    async def snapshot(self, scope, run_id, body, purpose):
        if purpose not in {"production", "evaluation_subject", "evaluation_judge", "unknown"}:
            raise ValueError("invalid usage purpose")
        identity = content_revision({"run_id": str(run_id), "purpose": purpose, "body": body})
        p = {
            **scope_params(scope),
            "id": identity,
            "run": run_id,
            "body": encoded(body),
            "purpose": purpose,
        }
        await self.session.execute(
            text("""INSERT INTO execution_configurations(id,run_id,body,purpose,owner_user_id,team_id,created_by)
        VALUES (:id,:run,CAST(:body AS jsonb),:purpose,:owner,:team,'execution-usage') ON CONFLICT DO NOTHING"""),
            p,
        )
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM execution_configurations WHERE scope_key=:scope AND id=:id"
                    ),
                    p,
                )
            )
            .mappings()
            .one()
        )
        if row["body"] != body or str(row["run_id"]) != str(run_id) or row["purpose"] != purpose:
            raise ValueError("configuration identity conflict")
        return identity

    async def admission_snapshot(self, scope, run_id, body, purpose):
        p = {**scope_params(scope), "run": run_id, "purpose": purpose}
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": p["scope"] + ":usage-admission:" + str(run_id)},
        )
        existing = await self.session.scalar(
            text(
                "SELECT id FROM execution_configurations WHERE scope_key=:scope AND run_id=:run AND body->>'stage'='admission' ORDER BY created_at,id LIMIT 1"
            ),
            p,
        )
        if existing is not None:
            return existing
        return await self.snapshot(scope, run_id, body, purpose)

    async def load_snapshot(self, scope, run_id, identity, purpose):
        body = await self.session.scalar(
            text(
                "SELECT body FROM execution_configurations WHERE scope_key=:scope AND run_id=:run AND id=:id AND purpose=:purpose"
            ),
            {**scope_params(scope), "run": run_id, "id": identity, "purpose": purpose},
        )
        if body is None:
            raise ValueError("admitted configuration unavailable")
        return body

    async def allocate(
        self,
        scope,
        *,
        run_id,
        activity_id,
        generation,
        claim_generation,
        configuration_id,
        request_snapshot,
        logical_group="invoke:0",
        activity_type=activity_types.MODEL_CALL,
    ):
        if activity_type not in {
            activity_types.MODEL_CALL,
            activity_types.TOOL_CALL,
            activity_types.RETRIEVAL_SEARCH,
            activity_types.KNOWLEDGE_BUILD,
            activity_types.CONTEXT_COMPACT,
        }:
            raise ValueError("physical dispatch activity unsupported")
        attempt = attempt_key(str(activity_id), generation, claim_generation)
        p = {
            **scope_params(scope),
            "run": run_id,
            "activity": activity_id,
            "activity_type": activity_type,
            "generation": generation,
            "claim": claim_generation,
            "attempt": attempt,
            "group": logical_group,
            "configuration": configuration_id,
            "request": encoded(request_snapshot),
        }
        # Claim row first, then logical allocation lock. Each call is a NEW permit,
        # never a reuse of a successfully authorized send. No SQL lock crosses HTTP.
        valid = await self.session.scalar(
            text("""SELECT 1 FROM execution_activity_tasks WHERE activity_id=:activity
        AND aggregate_id=CAST(CAST(:run AS uuid) AS text) AND request_generation=:generation AND claim_generation=:claim
        AND activity_type=:activity_type AND status='call_started'
        AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team FOR SHARE"""),
            p,
        )
        if not valid:
            raise ValueError("physical dispatch claim unavailable")
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": p["scope"] + ":dispatch:" + attempt + ":" + logical_group},
        )
        # The claim and allocation locks may block beyond either deadline. Use
        # wall-clock time only after both locks are held, before issuing a permit.
        valid = await self.session.scalar(
            text("""SELECT 1 FROM execution_activity_tasks WHERE activity_id=:activity
        AND claim_deadline>clock_timestamp() AND timeout_at>clock_timestamp()"""),
            p,
        )
        if not valid:
            raise ValueError("physical dispatch claim unavailable")
        ordinal = await self.session.scalar(
            text(
                "SELECT coalesce(max(ordinal),0)+1 FROM execution_model_dispatches WHERE scope_key=:scope AND attempt_id=:attempt AND logical_group=:group"
            ),
            p,
        )
        identity = str(uuid5(UUID(attempt), logical_group + ":" + str(ordinal)))
        await self.session.execute(
            text("""INSERT INTO execution_model_dispatches(call_identity,run_id,activity_id,generation,claim_generation,attempt_id,logical_group,ordinal,configuration_id,request_snapshot,owner_user_id,team_id,created_by)
        VALUES (:identity,:run,:activity,:generation,:claim,:attempt,:group,:ordinal,:configuration,CAST(:request AS jsonb),:owner,:team,'execution-usage')"""),
            {**p, "identity": identity, "ordinal": ordinal},
        )
        return identity

    async def record(self, scope, identity, fact):
        if fact.get("call_identity") != identity or not isinstance(fact.get("usage"), dict):
            raise ValueError("invalid usage identity")
        for name, value in fact["usage"].items():
            if (
                name.endswith("_tokens")
                and value is not None
                and (type(value) is not int or not 0 <= value <= 9223372036854775807)
            ):
                raise ValueError("invalid usage count")
        evidence = (
            (
                await self.session.execute(
                    text(
                        "SELECT d.configuration_id,c.body FROM execution_model_dispatches d JOIN execution_configurations c ON c.id=d.configuration_id AND c.scope_key=d.scope_key WHERE d.scope_key=:scope AND d.call_identity=:identity"
                    ),
                    {**scope_params(scope), "identity": identity},
                )
            )
            .mappings()
            .one_or_none()
        )
        if evidence is None:
            raise ValueError("invalid usage dispatch")
        if (
            fact.get("configuration_id", evidence["configuration_id"])
            != evidence["configuration_id"]
        ):
            raise ValueError("usage configuration mismatch")
        if "price" in evidence["body"]:
            from app.domain.models.execution_usage import PriceSnapshot

            price = PriceSnapshot.model_validate(evidence["body"]["price"])
            cost = price.cost(fact["usage"])
            expected_cost = str(cost) if cost is not None else None
            if (
                fact.get("price_revision") != price.revision
                or fact.get("cost_usd") != expected_cost
            ):
                raise ValueError("usage price snapshot mismatch")
        p = {**scope_params(scope), "identity": identity, "fact": encoded(fact)}
        await self.session.execute(
            text("""INSERT INTO execution_model_settlements(call_identity,fact,owner_user_id,team_id,created_by)
        VALUES (:identity,CAST(:fact AS jsonb),:owner,:team,'execution-usage') ON CONFLICT DO NOTHING"""),
            p,
        )
        row = await self.session.scalar(
            text(
                "SELECT fact FROM execution_model_settlements WHERE scope_key=:scope AND call_identity=:identity"
            ),
            p,
        )
        if row != fact:
            raise ValueError("conflicting physical usage callback")
        return row
