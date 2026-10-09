"""Pure command planning; activity claims and outcomes come from the runtime.

No event rows, projection facts, final states, or assumed successful provider
results are constructed here. The caller must persist every decision/effect via
the production orchestrator and acquire/settle real activity tasks.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid5

from scripts.seed_execution_visualization import event_count, run_identity, run_started_at

from app.domain.execution.activity import ActivityClaim, ActivityOutcome, ActivityRequest
from app.domain.execution.commands import CommandEnvelope, JsonValue
from app.domain.execution.run import RunAggregate
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.runtime_policy.snapshot import RunPolicySnapshot, validate_run_policy_snapshot


@dataclass(frozen=True)
class PlannedCommand:
    envelope: CommandEnvelope
    activity_id: UUID | None = None
    expected_request: ActivityRequest | None = None

    @property
    def command_type(self) -> str:
        return self.envelope.command_type

    def bind(
        self, *, claim: ActivityClaim | None = None, outcome: ActivityOutcome | None = None
    ) -> CommandEnvelope:
        if self.command_type not in {"MarkActivityCallStarted", "CompleteActivity"}:
            if claim is not None or outcome is not None:
                raise ValueError("unexpected claim/outcome for this command")
            return self.envelope
        if (
            claim is None
            or claim.request != self.expected_request
            or claim.request.activity_id != self.activity_id
            or claim.request.aggregate_type != "run"
            or claim.request.aggregate_id != self.envelope.stream_id
            or claim.request.generation != 0
            or claim.owner_user_id != self.envelope.owner_user_id
            or claim.team_id != self.envelope.team_id
            or claim.recovered_after_call_started
        ):
            raise ValueError("missing, foreign or uncertain activity claim")
        payload = {**self.envelope.payload, "claim_generation": claim.claim_generation}
        if self.command_type == "CompleteActivity":
            if outcome is None or outcome.status != "succeeded":
                raise ValueError("standard fixture requires an actual successful outcome")
            payload.update(
                result_ref=outcome.result_ref,
                result_summary=outcome.result_summary,
                decision_data=outcome.decision_data,
                public_data=outcome.public_data,
            )
        elif outcome is not None:
            raise ValueError("outcome cannot precede call start")
        return CommandEnvelope.model_validate(
            {**self.envelope.model_dump(mode="python"), "payload": payload}
        )


@dataclass(frozen=True)
class RunPlan:
    fixture_id: UUID
    seed: int
    index: int
    window_end: datetime
    owner_user_id: str | None
    team_id: str | None
    policy: RunPolicySnapshot
    activity_type: str
    activity_input: dict[str, JsonValue]

    def __post_init__(self):
        event_count(self.index)
        run_started_at(self.index, self.window_end)
        validate_run_policy_snapshot(self.policy)
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one owner scope required")
        if not self.activity_type.strip():
            raise ValueError("actual activity type required")

    @property
    def run_id(self) -> UUID:
        return run_identity(self.fixture_id, self.seed, self.index)

    def commands(self) -> Iterator[PlannedCommand]:
        """Lazily emit exact plans, never retaining 10m command objects in memory."""
        run_id = self.run_id
        now = run_started_at(self.index, self.window_end)
        registry = RunAggregate().command_registry
        version = 0

        def command(name, payload, activity_id=None, expected_request=None):
            nonlocal version
            envelope = CommandEnvelope(
                command_id=uuid5(run_id, f"command:{version}"),
                command_type=name,
                command_schema_version=registry.latest_version(name),
                stream_type="run",
                stream_id=str(run_id),
                expected_stream_version=version,
                owner_user_id=self.owner_user_id,
                team_id=self.team_id,
                correlation_id=run_id,
                causation_id=None,
                issued_at=now + timedelta(milliseconds=version),
                payload=payload,
            )
            version += 1
            return PlannedCommand(envelope, activity_id, expected_request)

        yield command(
            "CreateRun",
            {
                "family": self.policy.family.value,
                "source_entity_type": "capacity_fixture",
                "source_entity_id": str(self.fixture_id),
                "semantic_payload": {},
                "public_input": {},
                "policy_snapshot": self.policy.model_dump(mode="json"),
            },
        )
        yield command("StartRun", {})
        completed = 3331 if self.index < 10 else 31 if self.index < 1000 else 32
        for index in range(completed):
            aid = uuid5(run_id, f"activity:{index}")
            request_command = command(
                "RequestActivity",
                {
                    "activity_id": str(aid),
                    "activity_type": self.activity_type,
                    "timeout_at": (now + timedelta(minutes=30)).isoformat(),
                    "input_digest": "sha256:"
                    + sha256(canonical_json_bytes(self.activity_input)).hexdigest(),
                    "input_payload": self.activity_input,
                    "public_data": {"summary": "Capacity fixture activity"},
                },
            )
            yield request_command
            request_payload = request_command.envelope.payload
            expected_request = ActivityRequest(
                activity_id=aid,
                activity_type=self.activity_type,
                aggregate_type="run",
                aggregate_id=str(run_id),
                generation=0,
                timeout_at=request_payload["timeout_at"],
                input_ref=None,
                input_digest=request_payload["input_digest"],
                input_payload=self.activity_input,
            )
            payload = {"activity_id": str(aid), "generation": 0}
            yield command("MarkActivityCallStarted", payload, aid, expected_request)
            yield command("CompleteActivity", payload, aid, expected_request)
        if self.index < 1000:
            for _ in range(2):
                yield command("WaitRun", {"reason": "capacity_checkpoint"})
                yield command("ResumeRun", {})
        yield command("CompleteRun", {})
        if version != event_count(self.index):
            raise ValueError("fixture command count changed")
