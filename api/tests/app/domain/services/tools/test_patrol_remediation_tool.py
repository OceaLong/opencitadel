from app.domain.models.tool_policy import ApprovalMode, ToolCapability, ToolEffect, ToolIdempotency
from app.domain.services.tools.patrol_remediation import PATROL_REMEDIATION_POLICY, PatrolRemediationTool


async def test_patrol_remediation_policy_is_external_write_always_approved():
    """Governance contract: PATROL_REMEDIATION_POLICY must force every call
    through a ToolApprovalBatch (approval=ALWAYS) regardless of gate profile,
    unlike PATROL_SUBMIT_POLICY (approval=NEVER)."""
    assert PATROL_REMEDIATION_POLICY.capability == ToolCapability.EXECUTION
    assert PATROL_REMEDIATION_POLICY.effect == ToolEffect.EXTERNAL_WRITE
    assert PATROL_REMEDIATION_POLICY.idempotency == ToolIdempotency.IDEMPOTENT_WITH_KEY
    assert PATROL_REMEDIATION_POLICY.approval == ApprovalMode.ALWAYS
    assert PATROL_REMEDIATION_POLICY.concurrency_group == "patrol-remediation"


def test_patrol_execute_remediation_declares_named_idempotency_key_param():
    """ToolBatchExecutor._supports_idempotency_key requires IDEMPOTENT_WITH_KEY
    *and* an "idempotency_key" property in the tool schema *and* a same-named
    parameter on the bound method — otherwise the batch executor silently
    caps retries at 1 attempt instead of honoring the idempotency contract."""
    tool = PatrolRemediationTool(execute_fn=None, session_id="session-1")
    descriptor = tool.get_tool_descriptor("patrol_execute_remediation")
    schema_properties = descriptor.schema["function"]["parameters"]["properties"]
    assert "idempotency_key" in schema_properties
    assert "remediation_id" in schema_properties
    assert descriptor.schema["function"]["parameters"]["required"] == ["remediation_id", "idempotency_key"]
    import inspect

    assert "idempotency_key" in inspect.signature(descriptor.method).parameters


async def test_patrol_execute_remediation_delegates_to_execute_fn_and_binds_session():
    captured = {}

    async def execute_fn(**kwargs):
        captured.update(kwargs)
        return {"status": "executed"}

    tool = PatrolRemediationTool(execute_fn, session_id="session-1")
    result = await tool.patrol_execute_remediation(remediation_id="rem-1", idempotency_key="key-from-batch-executor")

    assert result.success is True
    assert captured == {
        "remediation_id": "rem-1",
        "session_id": "session-1",
        "idempotency_key": "key-from-batch-executor",
    }


async def test_patrol_execute_remediation_returns_failure_result_on_service_error():
    async def execute_fn(**kwargs):
        raise ValueError("binding mismatch")

    tool = PatrolRemediationTool(execute_fn, session_id="session-1")
    result = await tool.patrol_execute_remediation(remediation_id="rem-1", idempotency_key="key-1")

    assert result.success is False
    assert "binding mismatch" in result.message
