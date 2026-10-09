"""Version provenance must never leak a future or unconfirmed producer."""

import importlib.util
from uuid import uuid4

import pytest
from pydantic import ValidationError


def domain():
    name = "app.domain.models.artifact_provenance"
    assert importlib.util.find_spec(name) is not None, "typed artifact provenance is missing"
    return __import__(name, fromlist=["ArtifactProducer"])


def test_version_two_stays_in_future_and_pending_never_leaks():
    records = [
        {"version": 1, "position": 3},
        {"version": 2, "position": 8},
        {"version": 3, "position": 2, "binding_status": "pending"},
        {"version": 4, "position": None, "binding_status": "bound"},
        {"version": 5, "position": 2, "binding_status": "unavailable"},
    ]
    assert domain().visible_versions(records, 5) == records[:1]


def test_producer_is_immutable_scoped_and_operation_is_stable():
    from app.domain.models.scope import OwnerScope

    cls = domain().ArtifactProducer
    values = {
        "scope": OwnerScope.personal("f05"),
        "run_id": uuid4(),
        "activity_id": uuid4(),
        "generation": 0,
        "claim_generation": 1,
    }
    first, replay = cls(**values), cls(**values)
    assert first.operation_id == replay.operation_id
    assert first.operation_id != cls(**{**values, "claim_generation": 2}).operation_id
    with pytest.raises(ValidationError):
        first.activity_id = uuid4()
    with pytest.raises(ValidationError):
        cls(**{**values, "generation": -1})


def test_artifact_fact_after_terminal_preserves_state_and_has_no_effects():
    from datetime import UTC, datetime

    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate, RunState, RunStatus

    aggregate = RunAggregate()
    run = uuid4()
    state = RunState(
        run_id=run,
        status=RunStatus.FAILED,
        owner_user_id="f05",
        stream_version=8,
        terminal_event_id=uuid4(),
    )
    payload = {
        "operation_id": str(uuid4()),
        "artifact_id": str(uuid4()),
        "version": 1,
        "activity_id": str(uuid4()),
        "generation": 0,
        "claim_generation": 1,
    }
    command = CommandEnvelope(
        command_id=uuid4(),
        command_type="RecordArtifactVersionProduced",
        command_schema_version=1,
        stream_type="run",
        stream_id=str(run),
        owner_user_id="f05",
        team_id=None,
        correlation_id=uuid4(),
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload=payload,
    )
    assert "RecordArtifactVersionProduced" in aggregate.command_registry.registered_names()
    decision = aggregate.decide(state, command)
    assert [e.event_type for e in decision.events] == ["ArtifactVersionProduced"]
    assert not decision.activity_requests
    assert not decision.scheduled_commands


def test_real_write_contract_accepts_server_producer_and_has_repository():
    import inspect

    from app.application.services.artifact_service import ArtifactService

    assert "producer" in inspect.signature(ArtifactService.write_content).parameters
    assert importlib.util.find_spec(
        "app.infrastructure.repositories.db_artifact_provenance_repository"
    )


@pytest.mark.asyncio
async def test_artifact_catalog_injects_actual_context_without_model_schema_fields():
    from types import SimpleNamespace

    from app.domain.models.artifact import Artifact
    from tests.app.application.execution.test_agent_tool_catalog import CONTEXT, _catalog

    seen = []

    async def write(**kwargs):
        seen.append(kwargs)
        return Artifact(session_id="session", kind="doc", title="title")

    catalog = _catalog(None)[0]
    catalog._artifacts = SimpleNamespace(write_content=write)
    assert "activity_id" in type(CONTEXT).model_fields, "actual typed activity identity missing"
    context = CONTEXT.model_copy(update={"activity_id": uuid4(), "generation": 0})
    tool = catalog._artifact_tool(session_id="session", sandbox=None, context=context)
    result = await tool.artifact_write(kind="doc", title="title", content="hello")
    assert result.success
    assert seen[0]["producer"].activity_id == context.activity_id
    import inspect

    assert "producer" not in inspect.signature(tool.artifact_write).parameters


def test_provenance_rejects_unconfirmed_bound_or_pending_identity():
    cls = domain().ArtifactVersionProvenance
    args = {
        "id": uuid4(),
        "artifact_id": str(uuid4()),
        "version": 1,
        "producer_identity": str(uuid4()),
        "evidence_kind": "direct",
        "binding_status": "bound",
        "content_digest": "sha256:test",
    }
    with pytest.raises(ValidationError):
        cls(**args)
    with pytest.raises(ValidationError):
        cls(**{**args, "binding_status": "pending", "producer_run_id": uuid4()})


@pytest.mark.parametrize("experimental_null", [False, True])
def test_production_v1_baseline_and_null_only_compatibility_upcast(experimental_null):
    from app.domain.execution.registry import EventPayloads
    from app.domain.execution.run import ArtifactProductionPayload, RunAggregate

    aggregate = RunAggregate()
    assert set(ArtifactProductionPayload.model_fields) == {
        "operation_id",
        "artifact_id",
        "version",
        "activity_id",
        "generation",
        "claim_generation",
    }
    payload = {
        "operation_id": str(uuid4()),
        "artifact_id": str(uuid4()),
        "version": 1,
        "activity_id": str(uuid4()),
        "generation": 0,
        "claim_generation": 1,
    }
    if experimental_null:
        payload["invocation_id"] = None
    version, normalized = aggregate.event_registry.upcast(
        "ArtifactVersionProduced", 1, EventPayloads(public={}, internal=payload)
    )
    assert version == 2
    assert normalized.internal["invocation_id"] is None
    assert normalized.public == {}
    with pytest.raises(ValidationError):
        aggregate.event_registry.upcast(
            "ArtifactVersionProduced",
            1,
            EventPayloads(public={}, internal={**payload, "invocation_id": str(uuid4())}),
        )


def test_production_v1_and_v2_payload_goldens_are_frozen():
    import hashlib

    from app.domain.execution.run import ArtifactProductionPayload, ArtifactProductionPayloadV2
    from app.domain.execution.serialization import canonical_json_bytes

    payload = {
        "operation_id": "00000000-0000-0000-0000-000000000001",
        "artifact_id": "00000000-0000-0000-0000-000000000002",
        "version": 1,
        "activity_id": "00000000-0000-0000-0000-000000000003",
        "generation": 0,
        "claim_generation": 1,
    }
    assert (
        hashlib.sha256(
            canonical_json_bytes(ArtifactProductionPayload(**payload).model_dump(mode="json"))
        ).hexdigest()
        == "06d8eaa3812ef51753198e93be8d27d883101e9029b73c7c3fdc3b751589e6e0"
    )
    assert (
        hashlib.sha256(
            canonical_json_bytes(ArtifactProductionPayloadV2(**payload).model_dump(mode="json"))
        ).hexdigest()
        == "3b06d20ecdcb50ebc898fb531d758d21967f91127cd83098ed0fe8932f6c2413"
    )


def test_version_read_exposes_availability_and_immutable_evidence():
    cls = domain().ArtifactVersionProvenance
    assert "availability" in cls.model_fields
    record = cls(
        id=uuid4(),
        artifact_id=str(uuid4()),
        version=1,
        producer_identity="unknown",
        evidence_kind="unknown",
        binding_status="unavailable",
        content_digest=None,
        availability="unavailable",
        evidence={"reason": "user_authored"},
    )
    assert record.availability == "unavailable"
    with pytest.raises(TypeError):
        record.evidence["reason"] = "invented"
