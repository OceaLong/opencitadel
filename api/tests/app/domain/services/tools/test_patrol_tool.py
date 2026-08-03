from app.domain.services.tools.patrol import PATROL_SUBMIT_POLICY, PatrolTool


async def test_patrol_tool_policy_is_internal_idempotent_write():
    assert PATROL_SUBMIT_POLICY.effect.value == "workspace_write"
    assert PATROL_SUBMIT_POLICY.idempotency.value == "idempotent_with_key"
    assert PATROL_SUBMIT_POLICY.approval.value == "never"
    assert PATROL_SUBMIT_POLICY.concurrency_group == "patrol-run"


async def test_patrol_tool_binds_current_session_and_validates_results():
    captured = {}

    async def finalize(**kwargs):
        captured.update(kwargs)
        return {"status": "completed"}

    tool = PatrolTool(finalize, session_id="session-1")
    result = await tool.patrol_submit_results(
        run_id="run-1",
        idempotency_key="run-1:1",
        collector_capability_hash="a" * 64,
        results=[{"check_id": "check-1", "observation": {}}],
    )
    assert result.success is True
    assert captured["session_id"] == "session-1"
    assert captured["submissions"][0].check_id == "check-1"
