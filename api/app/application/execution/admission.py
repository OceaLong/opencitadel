"""Single application boundary for creating every execution Run family."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.command_ingress import CommandIngress, CommandSink
from app.application.execution.context_collection import ContextInputCollectionStore
from app.application.execution.decisions.context import context_enabled
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.execution.commands import CommandContext, JsonValue, RegisteredCommand
from app.domain.execution.errors import AdmissionLimitExceededError
from app.domain.execution.run import RunFamily
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import ExecutionPolicy
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot


def run_id_for_idempotency_key(idempotency_key: str) -> UUID:
    """Return the stable formal Run identity reserved by an admission key."""

    return uuid5(NAMESPACE_URL, f"opencitadel:run:{idempotency_key}")


def _in_admission_transaction(method):
    @wraps(method)
    async def invoke(self, **kwargs):
        sink = kwargs.get("command_sink")
        transaction = getattr(sink, "admission_transaction", None)
        if transaction is not None:
            async with transaction() as (bound_sink, work):
                return await method(
                    self, **{**kwargs, "command_sink": bound_sink, "inference_read_context": work}
                )
        if sink is None and self._transaction_factory is not None:
            async with self._transaction_factory() as work:
                result = await method(
                    self,
                    **{
                        **kwargs,
                        "command_sink": work.execution_commands,
                        "inference_read_context": work,
                    },
                )
                await work.commit()
                return result
        return await method(self, **kwargs)

    return invoke


class RunAdmissionService:
    def __init__(
        self,
        *,
        command_ingress: CommandIngress,
        activity_objects: ActivityObjectStore,
        policy_heads: PolicyHeadReader,
        max_active_runs_per_scope: int = 0,
        clock=None,
        configuration_resolver: Callable | None = None,
        definition_resolver: Callable | None = None,
        context_resource_resolver: Callable | None = None,
        transaction_factory: Callable | None = None,
    ) -> None:
        if max_active_runs_per_scope < 0:
            raise ValueError("max_active_runs_per_scope must not be negative")
        self._configuration_resolver = configuration_resolver
        self._definition_resolver = definition_resolver
        self._context_resource_resolver = context_resource_resolver
        self._transaction_factory = transaction_factory
        self._commands = command_ingress
        self._objects = activity_objects
        self._policy_heads = policy_heads
        self._max_active_runs_per_scope = max_active_runs_per_scope
        self._clock = clock or (lambda: datetime.now(UTC))

    @_in_admission_transaction
    async def admit(
        self,
        *,
        family: RunFamily,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
        private_input: dict[str, JsonValue] | None,
        public_input: dict[str, JsonValue],
        private_input_factory: (
            Callable[[ExecutionPolicy], Awaitable[dict[str, JsonValue]]] | None
        ) = None,
        workflow: dict[str, JsonValue] | None = None,
        idempotency_key: str | None = None,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        correlation_id: UUID | None = None,
        command_sink: CommandSink | None = None,
        usage_purpose: str = "production",
        configuration_resolver: Callable | None = None,
        inference_read_context: IUnitOfWork | None = None,
        input_adapter: Callable | None = None,
        judge_admission=None,
    ) -> UUID:
        resolved_run_id = run_id or (
            run_id_for_idempotency_key(idempotency_key) if idempotency_key else uuid4()
        )
        command_id = (
            uuid5(NAMESPACE_URL, f"opencitadel:admit:{idempotency_key}")
            if idempotency_key
            else uuid4()
        )
        now = self._clock()
        pin = manifest = None
        if self._definition_resolver is not None:
            pin, manifest = await self._definition_resolver(
                family=family,
                scope=owner_scope,
                command_id=command_id,
                run_id=resolved_run_id,
                read_context=inference_read_context,
                command_sink=command_sink,
            )
            manifest.require_pin(pin)
            if pin.family != family:
                raise ValueError("admission definition family mismatch")
        active_policy = await self._policy_heads.active_execution(
            require_fresh=True,
            now=now,
        )
        policy_snapshot = derive_run_policy_snapshot(active_policy, family)
        if (private_input is None) == (private_input_factory is None):
            raise ValueError("provide exactly one private Run input source")
        resolved_private_input = (
            await private_input_factory(active_policy.revision.policy)
            if private_input_factory is not None
            else private_input
        )
        if resolved_private_input is None:
            raise RuntimeError("private Run input resolution failed")
        resolved_private_input = dict(resolved_private_input)
        resolved_private_input.pop("_context_skill", None)
        resolved_private_input.pop("_context_files", None)
        if input_adapter is not None:
            # The adapter sees the actual request-pinned definition, after its
            # same-transaction receipt is fixed and before configuration capture.
            resolved_private_input = dict(await input_adapter(resolved_private_input, manifest))
        if context_enabled(manifest) and self._context_resource_resolver is not None:
            resolved_private_input = await self._context_resource_resolver(
                work=inference_read_context,
                scope=owner_scope,
                payload=resolved_private_input,
                run_id=resolved_run_id,
            )
        elif context_enabled(manifest) and (
            resolved_private_input.get("skill_id") or resolved_private_input.get("attachments")
        ):
            raise ValueError("CONTEXT_RESOURCE_DEPENDENCIES_UNAVAILABLE")
        resolved_private_input.pop("_context_collection", None)
        if context_enabled(manifest) and resolved_private_input.get("conversation"):
            if source_entity_type == "session":
                raise ValueError("CONTEXT_SESSION_REQUIRES_FORMAL_CUT")
            collection = await ContextInputCollectionStore(self._objects).freeze(
                resolved_run_id,
                owner_scope,
                resolved_private_input["conversation"],
                manifest.context_policy,
            )
            resolved_private_input.update(
                conversation=[], _context_collection=collection.model_dump(mode="json")
            )
        resolved_private_input.pop("_execution_definition", None)
        if pin is not None:
            resolved_private_input["_execution_definition"] = pin.model_dump(mode="json")
        if usage_purpose == "business":
            usage_purpose = "production"
        if usage_purpose not in {"production", "evaluation_subject", "evaluation_judge", "unknown"}:
            raise ValueError("invalid admitted usage purpose")
        # Trusted server call parameter, never a marker copied from private input.
        marker = {"purpose": usage_purpose}
        if configuration_resolver is not None:
            marker["configuration_id"] = await configuration_resolver(
                owner_scope,
                resolved_run_id,
                active_policy.revision.policy,
                **(
                    {"inference_read_context": inference_read_context}
                    if inference_read_context is not None
                    else {}
                ),
            )
        elif self._configuration_resolver is not None:
            configuration_id = await self._configuration_resolver(
                owner_scope,
                resolved_run_id,
                active_policy.revision.id,
                resolved_private_input,
                usage_purpose,
                **(
                    {"inference_read_context": inference_read_context}
                    if inference_read_context is not None
                    else {}
                ),
                **({"execution_policy": active_policy.revision.policy} if pin is not None else {}),
            )
            if configuration_id is not None:
                marker["configuration_id"] = configuration_id
        workflow = dict(workflow or {})
        workflow.pop("judge_protocol", None)
        workflow.pop("judge_intent", None)
        if judge_admission is not None:
            from app.domain.evaluation.judge_protocol import JudgeAdmission

            if (
                type(judge_admission) is not JudgeAdmission
                or judge_admission.run_id != resolved_run_id
                or family != RunFamily.ASK
                or usage_purpose != "evaluation_judge"
                or source_entity_type != "evaluation_judge"
                or source_entity_id != str(judge_admission.intent_id)
            ):
                raise ValueError("judge_admission_invalid")
            workflow.update(judge_protocol=1, judge_intent=str(judge_admission.intent_id))
        elif usage_purpose == "evaluation_judge":
            raise ValueError("judge_admission_required")
        resolved_private_input = {**resolved_private_input, "_execution_usage": marker}
        input_ref, input_digest = await self._objects.put_input(
            resolved_run_id,
            resolved_private_input,
            scope=owner_scope,
        )
        guard = getattr(self._objects, "guard_publication", None)
        if guard is not None:
            await guard(resolved_run_id, owner_scope, inference_read_context)
            if parent_run_id is not None:
                await guard(parent_run_id, owner_scope, inference_read_context)
        owner_user_id = None if owner_scope.team_id else owner_scope.user_id
        await self._commands.submit(
            RegisteredCommand(
                command_id=command_id,
                command_type="CreateRun",
                # Greenfield v1 baseline (EVOLUTION.md): the registry rejects
                # any submission that is not the latest registered version.
                command_schema_version=3,
                run_id=resolved_run_id,
                expected_stream_version=0,
                payload={
                    "family": family.value,
                    "definition_pin": pin.model_dump(mode="json") if pin is not None else None,
                    "definition_manifest": manifest.model_dump(mode="json")
                    if manifest is not None
                    else None,
                    "source_entity_type": source_entity_type,
                    "source_entity_id": source_entity_id,
                    "parent_run_id": (str(parent_run_id) if parent_run_id is not None else None),
                    "semantic_payload": {
                        "input_ref": input_ref,
                        "input_digest": input_digest,
                        **(workflow or {}),
                    },
                    "public_input": public_input,
                    "policy_snapshot": policy_snapshot.model_dump(mode="json"),
                },
            ),
            CommandContext(
                owner_user_id=owner_user_id,
                team_id=owner_scope.team_id,
                correlation_id=correlation_id or resolved_run_id,
                causation_id=None,
                issued_at=now,
            ),
            sink=command_sink,
            **(
                {"max_active_runs": self._max_active_runs_per_scope}
                if self._max_active_runs_per_scope > 0
                else {}
            ),
        )
        return resolved_run_id


__all__ = [
    "AdmissionLimitExceededError",
    "RunAdmissionService",
    "run_id_for_idempotency_key",
]
