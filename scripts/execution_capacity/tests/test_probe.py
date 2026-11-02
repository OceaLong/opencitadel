"""The additional step probe is distinct from standard formal-event hotspots."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.app.execution_test_support import run_execution_context_for


def test_probe_has_ten_thousand_real_request_slots_with_no_wait_padding():
    from scripts.execution_capacity.probe import ProbePlan
    from scripts.seed_execution_visualization import run_identity

    namespace = uuid4()
    policy = run_execution_context_for("ask").policy_snapshot
    plan = ProbePlan(namespace, 2, datetime.now(UTC), "probe-owner", policy)
    commands = list(plan.commands())
    assert len(commands) == 10002  # worker supplies 20,000 start/settlement facts
    requests = [c for c in commands if c.command_type == "RequestActivity"]
    assert len(requests) == len({c.envelope.payload["activity_id"] for c in requests}) == 10000
    assert plan.run_id != run_identity(namespace, 2, 0)
    assert requests[0].envelope.expected_stream_version == 2
    assert requests[-1].envelope.expected_stream_version == 29999
    assert commands[-1].envelope.expected_stream_version == 30002
    assert not any(c.command_type in {"WaitRun", "ResumeRun", "CompleteActivity"} for c in commands)


def test_probe_binding_rejects_shared_scope_or_namespace():
    from scripts.execution_capacity.probe import probe_binding

    fixture = str(uuid4())
    original = {"fixture_id": fixture, "principal_id": "standard", "session_id": "standard-session"}
    with pytest.raises(ValueError, match="independent"):
        probe_binding({**original, "probe": {**original}})
