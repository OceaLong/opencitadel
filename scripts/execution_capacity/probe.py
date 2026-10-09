"""Separate 10,000-attempt retrieval cohort, never a standard RunPlan index."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from scripts.execution_capacity.commands import PlannedCommand

from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.runtime_policy.snapshot import RunPolicySnapshot, validate_run_policy_snapshot


@dataclass(frozen=True)
class ProbePlan:
    fixture_id: UUID
    seed: int
    start: datetime
    owner_user_id: str
    policy: RunPolicySnapshot

    def __post_init__(self):
        validate_run_policy_snapshot(self.policy)
        if not self.owner_user_id or self.start.tzinfo is None:
            raise ValueError("probe requires an owned scope and aware time")

    @property
    def run_id(self):
        return uuid5(self.fixture_id, f"visible-step-probe:{self.seed}")

    def commands(self):
        registry = RunAggregate().command_registry

        def command(name, position, payload):
            return PlannedCommand(
                CommandEnvelope(
                    command_id=uuid5(self.run_id, f"probe-command:{position}"),
                    command_type=name,
                    command_schema_version=registry.latest_version(name),
                    stream_type="run",
                    stream_id=str(self.run_id),
                    expected_stream_version=position,
                    owner_user_id=self.owner_user_id,
                    team_id=None,
                    correlation_id=self.run_id,
                    causation_id=None,
                    issued_at=self.start + timedelta(milliseconds=position),
                    payload=payload,
                )
            )

        yield command("StartRun", 1, {})
        for index in range(10_000):
            yield command(
                "RequestActivity",
                2 + 3 * index,
                {
                    "activity_id": str(uuid5(self.run_id, f"retrieval:{index}")),
                    "activity_type": "retrieval.search",
                    "timeout_at": (self.start + timedelta(minutes=30)).isoformat(),
                    "input_digest": "sha256:" + "0" * 64,
                    "input_payload": {},
                    "public_data": {"summary": "Capacity retrieval step"},
                },
            )
        yield command("CompleteRun", 30002, {})


def probe_binding(binding):
    probe = binding["probe"]
    if (
        probe["fixture_id"] == binding["fixture_id"]
        or probe["principal_id"] == binding["principal_id"]
        or probe["session_id"] == binding["session_id"]
        or probe.get("team_id") is not None
    ):
        raise ValueError("probe requires independent namespace, personal scope and session")
    UUID(probe["fixture_id"])
    # Only bootstrap identity can differ. All deployment/source authority stays
    # anchored to the same host-verified binding, never caller-overridden here.
    fields = {
        "fixture_id",
        "principal_id",
        "session_id",
        "session_created_at",
        "model_id",
        "endpoint_id",
        "policy_revision",
    }
    if set(probe) != fields:
        raise ValueError("probe bootstrap binding fields differ")
    return {**binding, **probe}


async def construct_probe(resources, supervisor, journal, binding, manifest, *, host_fence):
    from scripts.execution_capacity.runtime import historical

    actual = probe_binding(binding)
    probe_manifest = {**manifest, "fixture_id": actual["fixture_id"]}
    return await historical(
        resources, supervisor, journal, actual, probe_manifest, host_fence=host_fence, _probe=True
    )
