"""Evidence-bound external operation queries and manual recovery acceptance."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from app.application.ports.execution_effect import EffectInvocationResult
from app.domain.errors import BadRequestError, ConflictError, NotFoundError, ValidationError
from app.domain.execution.commands import ActivityLeaseProof, CommandEnvelope
from app.domain.execution.effect import (
    EffectEvidenceRef,
    EffectObservation,
    EffectOperationSpec,
    EffectState,
)

MAX_EVIDENCE_BYTES = 1024 * 1024


def result_object(spec, result):
    locator = spec.provider_locator
    if locator.get("tool_call_id"):
        return {
            "kind": "tool",
            "message": {
                "role": "tool",
                "tool_call_id": locator["tool_call_id"],
                "name": locator["tool_name"],
                "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
            },
        }
    return result


def command_envelope(*, spec, name, payload, context=None, request_id=None):
    scope = context.run.owner_scope if context else None
    return CommandEnvelope(
        command_id=uuid4(),
        command_type=name,
        command_schema_version=1,
        stream_type="run",
        stream_id=str(spec.run_id),
        owner_user_id=(scope.user_id if scope and not scope.team_id else None),
        team_id=scope.team_id if scope else None,
        correlation_id=spec.run_id,
        causation_id=request_id or spec.activity_id,
        issued_at=datetime.now(UTC),
        payload=payload,
        activity_lease_proof=ActivityLeaseProof(
            activity_id=spec.activity_id,
            request_generation=spec.generation,
            claim_generation=context.claim_generation,
            worker_id=context.worker_id,
        )
        if context
        else None,
    )


class EffectService:
    def __init__(self, *, store, objects, files):
        self.store = store
        self.objects = objects
        self.files = files

    @staticmethod
    def _summary(row):
        effect = EffectState.model_validate(row["body"])
        spec = effect.spec
        can_query = spec.capability.value in {"dedupe_resolve", "resolve_only"}
        actions = []
        if not effect.abandoned and not effect.integrity_conflict:
            if effect.state in {"sending", "pending", "unknown"}:
                actions = (["query"] if can_query else []) + ["resolve", "abandon"]
            elif effect.awaiting_result:
                actions = ["supply_result", "abandon"]
        return {
            "operation_id": spec.operation_id,
            "run_id": spec.run_id,
            "invocation_id": spec.invocation_id,
            "activity_id": spec.activity_id,
            "revision": effect.revision,
            "state": effect.state,
            "capability": {"kind": spec.capability.value, "resolve": can_query, "cancel": False},
            "wait_reason": "external_evidence_conflict"
            if effect.integrity_conflict
            else "result_material_missing"
            if effect.awaiting_result
            else "external_outcome_unknown"
            if effect.state in {"sending", "pending", "unknown"}
            else None,
            "contract_digest": spec.contract_digest,
            "arguments_digest": spec.arguments_digest,
            "target_digest": spec.target_digest,
            "provider_identity": spec.provider_identity,
            "allowed_actions": actions,
            "accounting_state": "independent",
            "contract_drift": None,
            "verification_binding": {
                "action": spec.provider_locator.get("action")
                or spec.provider_locator.get("tool_name")
                or spec.provider_identity,
                "target": spec.target_digest,
            },
            "last_confirmed_fact": effect.latest_receipt.model_dump(mode="json")
            if effect.latest_receipt
            else None,
            "receipts": row.get("receipts", []),
            "query_history": row.get("query_history", []),
        }

    @staticmethod
    def _read_actions(summary, principal, scope):
        if principal.is_auditor or (
            scope.team_id
            and not principal.is_admin
            and principal.team_roles.get(scope.team_id) not in {"owner", "admin"}
        ):
            summary["allowed_actions"] = []
        return summary

    async def list_operations(self, **kwargs):
        return [
            self._read_actions(self._summary(row), kwargs["principal"], kwargs["scope"])
            for row in await self.store.list(**kwargs)
        ]

    async def get_operation(self, **kwargs):
        return self._read_actions(
            self._summary(await self.store.get(**kwargs, details=True)),
            kwargs["principal"],
            kwargs["scope"],
        )

    async def get_request(self, **kwargs):
        return await self.store.get_request(**kwargs)

    async def submit_query(self, **kwargs):
        prior = await self.store.existing_request(kind="query", body={}, **kwargs)
        if prior is not None:
            return prior
        row = await self.store.get(
            **{
                key: value
                for key, value in kwargs.items()
                if key not in {"expected_revision", "idempotency_key"}
            }
        )
        if "query" not in self._summary(row)["allowed_actions"]:
            raise ConflictError("Provider has no usable query capability")
        return await self.store.submit_request(kind="query", body={}, **kwargs)

    async def submit_abandon(self, *, reason, **kwargs):
        return await self.store.submit_request(kind="abandon", body={"reason": reason}, **kwargs)

    async def submit_resolution(
        self, *, decision, evidence_refs, reason, attestation, result_evidence_ref=None, **kwargs
    ):
        body = {
            "decision": decision,
            "evidence_refs": evidence_refs,
            "reason": reason,
            "attestation": attestation,
            "result_evidence_ref": result_evidence_ref,
        }
        prior = await self.store.existing_request(kind="resolution", body=body, **kwargs)
        if prior is not None:
            return prior
        row = await self.store.get(
            **{
                key: value
                for key, value in kwargs.items()
                if key not in {"expected_revision", "idempotency_key"}
            }
        )
        effect = EffectState.model_validate(row["body"])
        if effect.integrity_conflict or effect.abandoned:
            raise ConflictError("Effect operation is quarantined or abandoned")
        if decision not in {"applied", "not_applied"}:
            raise BadRequestError("A confirmed operation decision is required")
        if effect.state in {"applied", "not_applied"} and not (
            effect.state == decision == "applied" and effect.awaiting_result and result_evidence_ref
        ):
            raise ConflictError("Confirmed effect facts cannot be overwritten")
        await self.verify_manual(effect.spec, body, kwargs["scope"], kwargs["principal"])
        return await self.store.submit_request(kind="resolution", body=body, **kwargs)

    async def _file_package(self, spec, reference, scope, *, result=False):
        try:
            ref = EffectEvidenceRef.model_validate(reference)
            if not ref.ref.startswith("file://"):
                raise ValueError("evidence must be an uploaded immutable file")
            file_id = str(UUID(ref.ref.removeprefix("file://")))
            file = await self.files.get_file_info(file_id, scope)
            if file.size > MAX_EVIDENCE_BYTES:
                raise ValueError("evidence exceeds 1 MiB")
            raw = await self.files.read_fixed(file_id, ref.sha256, file.object_identity, scope)
            if len(raw) > MAX_EVIDENCE_BYTES:
                raise ValueError("evidence exceeds 1 MiB")
            package = json.loads(raw)
            if not isinstance(package, dict) or package.get("schema_version") != 1:
                raise ValueError("invalid evidence package schema")
            for key in ("operation_id", "contract_digest", "arguments_digest", "target_digest"):
                if package.get(key) != str(getattr(spec, key)):
                    raise ValueError("evidence belongs to a different operation intent")
            material = package.get("result" if result else "evidence")
            if result:
                if not isinstance(material, dict):
                    raise ValueError("result material must be an object")
                Draft202012Validator(spec.output_schema).validate(material)
            elif not material:
                raise ValueError("evidence material is required")
            return package
        except (ValueError, TypeError, NotFoundError, SchemaValidationError) as exc:
            raise ValidationError(
                "Effect evidence is unavailable, invalid, or bound to another intent"
            ) from exc

    async def verify_manual(self, spec, body, scope, principal):
        attestation = body.get("attestation", {})
        required = {"source", "action", "target", "occurred_at", "method", "responsible_actor"}
        if (
            any(not attestation.get(key) for key in required)
            or attestation.get("responsible_actor") != principal.user_id
            or attestation.get("target") != spec.target_digest
            or attestation.get("action")
            != (
                spec.provider_locator.get("action")
                or spec.provider_locator.get("tool_name")
                or spec.provider_identity
            )
        ):
            raise ValidationError(
                "Human attestation does not match responsible actor and frozen action"
            )
        try:
            occurred = datetime.fromisoformat(str(attestation["occurred_at"]))
            if occurred.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise ValidationError("Attestation requires an aware occurrence time") from exc
        if not body.get("evidence_refs") or len(body["evidence_refs"]) > 20:
            raise ValidationError("One to twenty evidence references are required")
        packages = [await self._file_package(spec, ref, scope) for ref in body["evidence_refs"]]
        result = None
        if body.get("result_evidence_ref"):
            if body["decision"] != "applied":
                raise ValidationError("Only an applied operation can attach result material")
            result = (
                await self._file_package(spec, body["result_evidence_ref"], scope, result=True)
            )["result"]
        return packages, result

    async def persist_observation(
        self, spec, *, state, source, evidence, result=None, reason="", receipt_id=None
    ):
        evidence_ref = await self.objects.put_result(spec.activity_id, evidence, run_id=spec.run_id)
        result_ref = (
            await self.objects.put_result(
                spec.activity_id, result_object(spec, result), run_id=spec.run_id
            )
            if result is not None
            else None
        )
        return EffectObservation(
            receipt_id=receipt_id or uuid4(),
            state=state,
            source=source,
            evidence=EffectEvidenceRef(
                ref=evidence_ref, sha256=evidence_ref.rsplit("/", 1)[-1][:-5]
            ),
            observed_at=datetime.now(UTC),
            result_ref=result_ref,
            result_digest=result_ref.rsplit("/", 1)[-1][:-5] if result_ref else None,
            reason=reason[:2000],
        )

    @staticmethod
    def _require_context_binding(spec, context):
        if context.run.run_id != spec.run_id or any(
            getattr(context, field, None) is not None
            and getattr(context, field) != getattr(spec, field)
            for field in ("activity_id", "generation")
        ):
            raise ConflictError("Effect operation does not match the Activity context")

    async def _journal_result(self, spec, effect):
        if effect.spec != spec:
            raise ConflictError("Effect operation is bound to another invocation intent")
        if effect.integrity_conflict or effect.abandoned:
            return EffectInvocationResult("unknown", spec.operation_id)
        if effect.state == "prepared":
            return None
        result = None
        if effect.state == "applied" and effect.result_ref:
            try:
                if not effect.result_ref.startswith(f"execution/results/{spec.activity_id}/"):
                    raise ValueError("result belongs to another Activity")
                value = await self.objects.load_result(effect.result_ref)
                if spec.provider_locator.get("tool_call_id"):
                    message = value["message"]
                    if (
                        value.get("kind") != "tool"
                        or message.get("role") != "tool"
                        or message.get("tool_call_id") != spec.provider_locator["tool_call_id"]
                        or message.get("name") != spec.provider_locator["tool_name"]
                    ):
                        raise ValueError("result does not match the frozen tool call")
                    result = json.loads(message["content"])
                else:
                    result = value
                if not isinstance(result, dict):
                    raise TypeError("result must be an object")
                Draft202012Validator(spec.output_schema).validate(result)
            except (ValueError, TypeError, KeyError, SchemaValidationError) as exc:
                raise ValidationError(
                    "Persisted effect result does not match its invocation"
                ) from exc
        return EffectInvocationResult(effect.state, spec.operation_id, result, effect.result_ref)

    async def recover(self, spec, context):
        """Read an exact invocation's journal without rediscovery or external IO."""
        spec = EffectOperationSpec.model_validate(spec)
        self._require_context_binding(spec, context)
        try:
            row = await self.store.get(
                operation_id=spec.operation_id,
                run_id=spec.run_id,
                scope=context.run.owner_scope,
            )
        except NotFoundError:
            return None
        return await self._journal_result(spec, EffectState.model_validate(row["body"]))

    async def invoke(self, spec, context, send_callable, *, preflight=None, interpret=None):
        spec = EffectOperationSpec.model_validate(spec)
        self._require_context_binding(spec, context)
        if context.require_active_lease:
            context.require_active_lease()
        recovered = await self.recover(spec, context)
        if recovered is not None:
            return recovered
        await self.store.worker_command(
            command_envelope(
                spec=spec,
                name="PrepareEffectOperation",
                payload={"spec": spec.model_dump(mode="json")},
                context=context,
            ),
            preflight=preflight,
        )
        row = await self.store.get(
            operation_id=spec.operation_id, run_id=spec.run_id, scope=context.run.owner_scope
        )
        effect = EffectState.model_validate(row["body"])
        recovered = await self._journal_result(spec, effect)
        if recovered is not None:
            return recovered
        await self.store.worker_command(
            command_envelope(
                spec=spec,
                name="StartEffectDispatch",
                payload={
                    "operation_id": str(spec.operation_id),
                    "expected_revision": effect.revision,
                    "dispatch_proof": {
                        "activity_id": str(spec.activity_id),
                        "request_generation": spec.generation,
                        "claim_generation": context.claim_generation,
                        "worker_id": context.worker_id,
                    },
                },
                context=context,
            ),
            preflight=preflight,
        )
        if context.require_active_lease:
            context.require_active_lease()
        try:
            result = await send_callable()
            if interpret:
                state, complete = interpret(result)
            else:
                Draft202012Validator(spec.output_schema).validate(result)
                state, complete = (
                    ("applied", result)
                    if isinstance(result, dict) and result.get("success") is True
                    else ("unknown", None)
                )
            observation = await self.persist_observation(
                spec,
                state=state,
                source="provider_receipt",
                evidence={"operation_id": str(spec.operation_id), "response": result},
                result=complete,
            )
        except asyncio.CancelledError:
            # Sending intent is durable; a new worker may only query/verify it.
            raise
        except Exception as exc:  # noqa: BLE001 - a sent write must retain unknown on any transport/decoder failure.
            observation = await self.persist_observation(
                spec,
                state="unknown",
                source="transport_unknown",
                evidence={"operation_id": str(spec.operation_id), "error_type": type(exc).__name__},
                reason="External outcome requires verification",
            )
            complete = None
        try:
            await self.store.worker_command(
                command_envelope(
                    spec=spec,
                    name="RecordEffectObservation",
                    payload={
                        "operation_id": str(spec.operation_id),
                        "expected_revision": effect.revision + 1,
                        "observation": observation.model_dump(mode="json"),
                    },
                    context=context,
                )
            )
        except ConflictError:
            # An expired claim cannot append facts. Persist evidence for the
            # separate trusted reconciler; it cannot revive a cancelled Run.
            await self.store.defer_observation(spec, context, observation)
            return EffectInvocationResult("unknown", spec.operation_id)
        return EffectInvocationResult(
            observation.state, spec.operation_id, complete, observation.result_ref
        )
