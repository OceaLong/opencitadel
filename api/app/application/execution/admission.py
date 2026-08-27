"""Single application boundary for creating every execution Run family."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.command_ingress import CommandIngress, CommandSink
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.execution.commands import CommandContext, JsonValue, RegisteredCommand
from app.domain.execution.run import RunFamily
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ExecutionPolicy
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot


def run_id_for_idempotency_key(idempotency_key: str) -> UUID:
    """Return the stable formal Run identity reserved by an admission key."""

    return uuid5(NAMESPACE_URL, f"opencitadel:run:{idempotency_key}")


class RunAdmissionService:
    def __init__(
        self,
        *,
        command_ingress: CommandIngress,
        activity_objects: ActivityObjectStore,
        policy_heads: PolicyHeadReader,
        clock=None,
    ) -> None:
        self._commands = command_ingress
        self._objects = activity_objects
        self._policy_heads = policy_heads
        self._clock = clock or (lambda: datetime.now(UTC))

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
        input_ref, input_digest = await self._objects.put_input(
            resolved_run_id,
            resolved_private_input,
        )
        owner_user_id = None if owner_scope.team_id else owner_scope.user_id
        await self._commands.submit(
            RegisteredCommand(
                command_id=command_id,
                command_type="CreateRun",
                command_schema_version=2,
                run_id=resolved_run_id,
                expected_stream_version=0,
                payload={
                    "family": family.value,
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
        )
        return resolved_run_id


__all__ = ["RunAdmissionService", "run_id_for_idempotency_key"]
