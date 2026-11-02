"""Bounded durable recovery: query capabilities, or evidence-backed human facts."""

import logging
from datetime import UTC, datetime

from app.domain.errors import AppException, ConflictError
from app.domain.execution.commands import ActivityLeaseProof, CommandEnvelope
from app.domain.execution.effect import EffectObservation, EffectState
from app.domain.models.scope import OwnerScope, Principal

logger = logging.getLogger(__name__)


class EffectReconciler:
    def __init__(self, *, service, resolvers=None, authority=None, after_commit=None):
        self.service = service
        self.store = service.store
        self.resolvers = resolvers or {}
        self.authority = authority
        self.after_commit = after_commit

    async def process_pending(self):
        processed = 0
        for _ in range(20):
            row = await self.store.claim_request()
            if row is None:
                break
            try:
                # A queued human request does not preserve its actor's access.
                # Recheck before reading/copying evidence, not only at commit.
                await self.store.authorize_request(row)
                operation = await self.store.get(operation_id=row["operation_id"])
                effect = EffectState.model_validate(operation["body"])
                spec = effect.spec
                scope = (
                    OwnerScope.team(
                        (row["principal"] or {}).get("user_id", "execution-kernel"), row["team_id"]
                    )
                    if row["team_id"]
                    else OwnerScope.personal(row["owner_user_id"])
                )
                if (
                    effect.revision != row["expected_revision"]
                    and row["kind"] != "provider_observation"
                ):
                    raise ConflictError("EFFECT_REVISION_CONFLICT")
                if row["kind"] == "abandon":
                    name = "AbandonEffectResolution"
                    payload = {"reason": str(row["body"]["reason"])[:2000]}
                elif row["kind"] == "provider_observation":
                    proof = ActivityLeaseProof.model_validate(row["body"]["dispatch_proof"])
                    if proof != effect.dispatch_proof:
                        raise ConflictError("EFFECT_SEND_AUTHORITY_MISMATCH")
                    observation = EffectObservation.model_validate(row["body"]["observation"])
                    prefix = f"execution/results/{spec.activity_id}/"
                    if (
                        not observation.evidence.ref.startswith(prefix)
                        or observation.evidence.sha256
                        != observation.evidence.ref.rsplit("/", 1)[-1][:-5]
                    ):
                        raise ConflictError("EFFECT_RECEIPT_BINDING_MISMATCH")
                    evidence = await self.service.objects.load_result(observation.evidence.ref)
                    if evidence.get("operation_id") != str(spec.operation_id):
                        raise ConflictError("EFFECT_RECEIPT_BINDING_MISMATCH")
                    if observation.result_ref:
                        if not observation.result_ref.startswith(prefix):
                            raise ConflictError("EFFECT_RESULT_BINDING_MISMATCH")
                        await self.service.objects.load_result(observation.result_ref)
                    name = "ResolveActivityOutcome"
                    payload = {"observation": observation.model_dump(mode="json")}
                elif row["kind"] == "reconcile_fact":
                    if effect.latest_receipt is None or effect.state not in {
                        "applied",
                        "not_applied",
                    }:
                        raise ConflictError("EFFECT_CONFIRMED_FACT_REQUIRED")
                    observation = effect.latest_receipt.model_copy(
                        update={"receipt_id": row["request_id"]}
                    )
                    name = "ResolveActivityOutcome"
                    payload = {"observation": observation.model_dump(mode="json")}
                elif row["kind"] == "local_not_sent":
                    if effect.state != "prepared":
                        raise ConflictError("EFFECT_DISPATCH_INTENT_PRESENT")
                    observation = await self.service.persist_observation(
                        spec,
                        state="not_applied",
                        source="kernel_not_sent",
                        evidence={
                            "operation_id": str(spec.operation_id),
                            "prepared_revision": effect.revision,
                            "reason": "Activity settled before any durable send authorization",
                        },
                        receipt_id=row["request_id"],
                    )
                    name = "ResolveActivityOutcome"
                    payload = {"observation": observation.model_dump(mode="json")}
                elif row["kind"] == "resolution":
                    principal = Principal.model_validate(row["principal"])
                    packages, result = await self.service.verify_manual(
                        spec, row["body"], scope, principal
                    )
                    observation = await self.service.persist_observation(
                        spec,
                        state=row["body"]["decision"],
                        source="human_verified",
                        evidence={
                            "operation_id": str(spec.operation_id),
                            "evidence_packages": packages,
                            "attestation": row["body"]["attestation"],
                            "reason": row["body"]["reason"],
                        },
                        result=result,
                        reason=row["body"]["reason"],
                        receipt_id=row["request_id"],
                    )
                    name = (
                        "SupplyActivityResult"
                        if effect.awaiting_result and result is not None
                        else "ResolveActivityOutcome"
                    )
                    payload = {"observation": observation.model_dump(mode="json")}
                else:
                    if (
                        effect.abandoned
                        or effect.integrity_conflict
                        or effect.state in {"applied", "not_applied"}
                    ):
                        raise ConflictError("EFFECT_ALREADY_CONFIRMED")
                    if (
                        spec.capability.value not in {"dedupe_resolve", "resolve_only"}
                        or spec.provider_identity not in self.resolvers
                    ):
                        raise ConflictError("EFFECT_QUERY_UNSUPPORTED")
                    if (
                        spec.query_deadline and datetime.now(UTC) >= spec.query_deadline
                    ) or await self.store.query_count(spec.operation_id) > spec.query_limit:
                        raise ConflictError("EFFECT_QUERY_BUDGET_EXHAUSTED")
                    # Authorization is checked before external I/O and once more
                    # when the trusted result command is consumed.
                    await self.store.authorize_request(row)
                    if self.authority:
                        await self.authority(spec, scope)
                    outcome = await self.resolvers[spec.provider_identity](spec, scope)
                    observation = await self.service.persist_observation(
                        spec,
                        state=outcome["state"],
                        source="provider_receipt",
                        evidence=outcome["evidence"],
                        result=outcome.get("result"),
                        receipt_id=row["request_id"],
                    )
                    name = "ResolveActivityOutcome"
                    payload = {"observation": observation.model_dump(mode="json")}
                payload.update(
                    operation_id=str(spec.operation_id),
                    expected_revision=effect.revision,
                    request_id=str(row["request_id"]),
                )
                command = CommandEnvelope(
                    command_id=row["request_id"],
                    command_type=name,
                    command_schema_version=1,
                    stream_type="run",
                    stream_id=str(spec.run_id),
                    owner_user_id=row["owner_user_id"],
                    team_id=row["team_id"],
                    correlation_id=spec.run_id,
                    causation_id=row["request_id"],
                    issued_at=row["created_at"],
                    payload=payload,
                )
                accepted = await self.store.finish_request(row, command=command)
                if accepted and self.after_commit:
                    await self.after_commit(spec, scope)
            except AppException as exc:
                await self.store.finish_request(row, error_code=type(exc).__name__[:128])
            except Exception:  # noqa: BLE001 - unresolved provider outcomes never prove failure.
                # A resolver exception never proves a failed external write.
                # The bounded durable request exposes failure for a new query.
                await self.store.finish_request(row, error_code="EFFECT_RESOLUTION_UNAVAILABLE")
            processed += 1
        if self.after_commit:
            for operation in await self.store.projection_work():
                effect = EffectState.model_validate(operation["body"])
                if effect.state in {"prepared", "sending"}:
                    continue
                scope = (
                    OwnerScope.team("execution-kernel", operation["team_id"])
                    if operation["team_id"]
                    else OwnerScope.personal(operation["owner_user_id"])
                )
                try:
                    await self.after_commit(effect.spec, scope)
                    await self.store.mark_projected(effect.spec.operation_id, effect.revision)
                except Exception:
                    logger.exception(
                        "Effect projection will retry operation %s", effect.spec.operation_id
                    )
                    continue
        return processed
