"""Single application boundary for creating every execution Run family."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
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


class AdmissionLimitExceededError(ValueError):
    """Per-scope active-Run ceiling reached; new Runs are refused (K2-8).

    A ValueError subclass so existing admission error handling (which surfaces
    admit()'s ValueErrors to the caller) needs no new plumbing; the message is
    the stable machine-readable code.
    """

    def __init__(self, *, limit: int, active: int) -> None:
        super().__init__("ADMISSION_LIMIT_EXCEEDED")
        self.limit = limit
        self.active = active


class ActiveRunCounter(Protocol):
    async def count_active_runs(self, *, owner_scope: OwnerScope) -> int: ...


class RunAdmissionService:
    def __init__(
        self,
        *,
        command_ingress: CommandIngress,
        activity_objects: ActivityObjectStore,
        policy_heads: PolicyHeadReader,
        active_run_counter: ActiveRunCounter | None = None,
        max_active_runs_per_scope: int = 0,
        clock=None,
    ) -> None:
        if max_active_runs_per_scope < 0:
            raise ValueError("max_active_runs_per_scope must not be negative")
        self._commands = command_ingress
        self._objects = activity_objects
        self._policy_heads = policy_heads
        # Explicit backpressure boundary (K2-8): 0 (or no counter) disables the
        # per-scope active-Run ceiling.
        self._active_run_counter = active_run_counter
        self._max_active_runs_per_scope = max_active_runs_per_scope
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
        if self._active_run_counter is not None and self._max_active_runs_per_scope > 0:
            # Check-then-act on the projection is intentionally advisory: a
            # concurrent admit may overshoot the ceiling by a few Runs, which is
            # acceptable for a backpressure boundary (the invariant guard is the
            # database, not this counter).
            active = await self._active_run_counter.count_active_runs(owner_scope=owner_scope)
            if active >= self._max_active_runs_per_scope:
                raise AdmissionLimitExceededError(
                    limit=self._max_active_runs_per_scope,
                    active=active,
                )
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
                # Greenfield v1 baseline (EVOLUTION.md): the registry rejects
                # any submission that is not the latest registered version.
                command_schema_version=1,
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


__all__ = [
    "ActiveRunCounter",
    "AdmissionLimitExceededError",
    "RunAdmissionService",
    "run_id_for_idempotency_key",
]
