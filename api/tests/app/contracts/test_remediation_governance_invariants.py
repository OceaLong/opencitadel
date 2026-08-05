"""Executable governance invariants for Ops Patrol Remediation execution.

Exercises the real PatrolRemediationTool through the real ToolBatchExecutor
(the same governed batch/approval machinery every strict-gate session runs
through), backed by an in-memory PatrolRepository/MCPServerRepository and a
fake Actuator client — so these tests prove the four security invariants
from the phase-3 Task 3 brief hold at the actual code path an Agent session
would exercise, not just at the PatrolRemediationService unit level:

1. Zero execution before approval.
2. params_hash binding survives from proposal through to execution.
3. Actuator capability-hash drift is rejected before any write call.
4. AUDITOR can create nor approve a remediation.

Fixture/fake shapes mirror tests/app/domain/services/agents/test_tool_batch_executor.py
(_ApprovalRepository, _call) and
tests/app/application/services/test_patrol_remediation_service.py (Repo/Uow
in-memory PatrolRepository stand-in), per the brief's instruction to model
this file on tests/app/contracts/test_agent_governance_invariants.py:296-351.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.patrol import (
    PatrolRemediation,
    PatrolRemediationAction,
    PatrolRemediationStatus,
    patrol_remediation_params_hash,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_approval import ApprovalStatus
from app.domain.services.agents.tool_batch_executor import ToolBatchExecutor
from app.domain.services.tools.patrol_remediation import PatrolRemediationTool
from app.interfaces.auth_dependencies import require_non_auditor
from app.interfaces.endpoints.patrol_routes import router as patrol_router
from app.interfaces.endpoints.session_routes import router as session_router


SCOPE = OwnerScope.personal("user-1")


class _PatrolRepo:
    def __init__(self) -> None:
        self.remediations: dict[str, PatrolRemediation] = {}

    async def get_remediation(self, remediation_id, scope=None, for_update=False):
        return self.remediations.get(remediation_id)

    async def save_remediation(self, remediation):
        self.remediations[remediation.id] = remediation
        return remediation


class _MCPServerRepo:
    def __init__(self, server: MCPServerRecord) -> None:
        self._server = server

    async def get_by_name(self, name, scope=None):
        return self._server if name == self._server.name else None


class _Uow:
    def __init__(self, patrol: _PatrolRepo, server: MCPServerRecord) -> None:
        self.patrol = patrol
        self.mcp_server = _MCPServerRepo(server)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _FakeActuatorClient:
    """Records every call. get_capabilities always returns the single
    `live_hash` configured at construction — PatrolRemediationService.execute()
    now makes exactly *one* live get_capabilities() call per invocation and
    compares it against the pre-approval baseline persisted on the
    remediation record (remediation.actuator_capability_hash), not against a
    second call to this same fake (a same-process back-to-back self-compare
    can never observe real drift — see test_capability_drift_rejects_execution
    for why the baseline must come from outside this fake)."""

    def __init__(self, live_hash: str = "h" * 64, envelope: dict | None = None) -> None:
        self._live_hash = live_hash
        self._envelope = envelope or {"action_outcome": "applied", "before": {"replicas": 1}, "after": {"replicas": 1}}
        self.get_capabilities_calls = 0
        self.execute_action_calls: list[dict] = []

    async def get_capabilities(self, server, *, timeout: int = 15):
        self.get_capabilities_calls += 1
        return {"overall_capability_hash": self._live_hash}

    async def execute_action(self, server, tool, arguments, *, timeout: int = 30):
        self.execute_action_calls.append({"tool": tool, "arguments": dict(arguments)})
        return dict(self._envelope)


class _ApprovalRepository:
    """Copied from tests/app/domain/services/agents/test_tool_batch_executor.py
    (_ApprovalRepository) — the minimal in-memory ResourceGovernanceRepository
    stand-in that supports the full preflight -> execute (waiting) -> decide
    -> resume lifecycle ToolBatchExecutor drives."""

    def __init__(self) -> None:
        self.batches: dict[str, object] = {}

    async def save_approval_batch(self, batch):
        self.batches[batch.id] = batch

    async def get_pending_approval_batch(self, session_id):
        pending = [b for b in self.batches.values() if b.session_id == session_id and b.status == ApprovalStatus.PENDING]
        return pending[-1] if pending else None

    async def get_approval_batch(self, batch_id):
        return self.batches.get(batch_id)

    async def decide_approval_call(self, tool_call_id, status, decided_by):
        for batch_id, batch in self.batches.items():
            for index, call in enumerate(batch.calls):
                if call.tool_call_id != tool_call_id:
                    continue
                calls = list(batch.calls)
                calls[index] = call.model_copy(update={"status": status, "decided_by": decided_by, "decided_at": datetime.now(timezone.utc)})
                batch_status = ApprovalStatus.PENDING
                if all(item.status != ApprovalStatus.PENDING for item in calls):
                    batch_status = ApprovalStatus.REJECTED if any(item.status == ApprovalStatus.REJECTED for item in calls) else ApprovalStatus.APPROVED
                self.batches[batch_id] = batch.model_copy(update={"calls": calls, "status": batch_status})
                return calls[index]
        return None

    async def consume_approval_batch(self, batch_id):
        batch = self.batches.get(batch_id)
        if batch is None:
            return None
        if batch.expires_at <= datetime.now(timezone.utc):
            expired = batch.model_copy(update={"status": ApprovalStatus.EXPIRED})
            self.batches[batch_id] = expired
            return expired
        if batch.status == ApprovalStatus.APPROVED:
            stored = batch.model_copy(update={"status": ApprovalStatus.CONSUMED})
            self.batches[batch_id] = stored
            return stored.model_copy(update={"execution_claimed": True})
        return batch


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _make_server() -> MCPServerRecord:
    return MCPServerRecord(id="server-actuator-1", name="ops-actuator", url="https://actuator.example/mcp")


def _make_remediation(
    *,
    remediation_id: str = "rem-1",
    session_id: str = "session-1",
    capability_baseline: str | None = "h" * 64,
) -> PatrolRemediation:
    """capability_baseline defaults to a set value, standing in for what
    TaskRunnerFactory._establish_remediation_capability_baseline persists
    *before* the tool is ever exposed to the LLM (i.e. before any approval
    exists) — these service-level tests start from that already-persisted
    state rather than re-driving TaskRunnerFactory, which has its own
    dedicated coverage in test_patrol_task_runner_factory.py."""
    action = PatrolRemediationAction.RESTART_WORKLOAD
    namespace, workload, kind, params = "opencitadel", "deployment/api", "Deployment", {}
    return PatrolRemediation(
        id=remediation_id,
        pack_id="pack-1",
        run_id="run-1",
        finding_id="finding-1",
        check_result_id="check-result-1",
        fingerprint="f" * 64,
        session_id=session_id,
        action=action,
        target_namespace=namespace,
        target_workload=workload,
        target_kind=kind,
        params=params,
        params_hash=patrol_remediation_params_hash(action.value, namespace, workload, kind, params),
        idempotency_key=f"rem:{remediation_id}",
        actuator_capability_hash=capability_baseline,
        status=PatrolRemediationStatus.PROPOSED,
        created_by="user-1",
    )


def _build(*, capability_baseline: str | None = "h" * 64, live_hash: str = "h" * 64, envelope: dict | None = None):
    remediation = _make_remediation(capability_baseline=capability_baseline)
    repo = _PatrolRepo()
    repo.remediations[remediation.id] = remediation
    server = _make_server()
    actuator = _FakeActuatorClient(live_hash=live_hash, envelope=envelope)
    service = PatrolRemediationService(lambda: _Uow(repo, server), actuator_client=actuator)

    async def execute_fn(**kwargs):
        return await service.execute(scope=SCOPE, **kwargs)

    tool = PatrolRemediationTool(execute_fn, session_id=remediation.session_id)
    approval_repo = _ApprovalRepository()
    executor = ToolBatchExecutor(session_id=remediation.session_id, tools=[tool], approval_repository=approval_repo)
    return remediation, repo, actuator, executor, approval_repo


@pytest.mark.asyncio
async def test_zero_execution_before_approval():
    """patrol_execute_remediation called inside a fresh session -> batch
    PENDING, actuator sees zero calls, remediation stays PROPOSED (the
    session, governed generically by ToolApprovalBatch, is left waiting)."""
    remediation, repo, actuator, executor, approval_repo = _build()

    batch = await executor.preflight(
        [_call("tc1", "patrol_execute_remediation", {"remediation_id": remediation.id, "idempotency_key": "llm-supplied-key"})]
    )
    result = await executor.execute(batch)

    assert result.waiting is True
    assert actuator.get_capabilities_calls == 0
    assert actuator.execute_action_calls == []
    persisted = approval_repo.batches[batch.batch_id]
    assert persisted.status == ApprovalStatus.PENDING
    assert persisted.calls[0].status == ApprovalStatus.PENDING
    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.PROPOSED


@pytest.mark.asyncio
async def test_approved_call_executes_exactly_once_with_stable_key():
    """After approval + resume, the Actuator receives exactly one call, and
    its idempotency_key is the remediation record's own persisted value —
    never the LLM-/batch-executor-supplied one."""
    remediation, repo, actuator, executor, approval_repo = _build()

    batch = await executor.preflight(
        [_call("tc1", "patrol_execute_remediation", {"remediation_id": remediation.id, "idempotency_key": "llm-supplied-key"})]
    )
    await executor.execute(batch)
    await approval_repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "approver-1")

    result = await executor.resume(batch.batch_id, actor_id="approver-1")

    assert result.rejected_reason is None
    assert result.calls[0].result.success is True
    # Exactly one live capability read per execute() call — the baseline it
    # is compared against comes from the persisted, pre-approval
    # remediation.actuator_capability_hash, not from a second live call.
    assert actuator.get_capabilities_calls == 1
    assert len(actuator.execute_action_calls) == 1
    call = actuator.execute_action_calls[0]
    assert call["tool"] == "restart_workload"
    assert call["arguments"]["idempotency_key"] == remediation.idempotency_key
    assert call["arguments"]["idempotency_key"] != "llm-supplied-key"
    assert repo.remediations[remediation.id].status == PatrolRemediationStatus.EXECUTED

    # A second resume against the same (now-consumed) batch must not call
    # the Actuator again — approval_consumed, not a second write.
    second = await executor.resume(batch.batch_id, actor_id="approver-1")
    assert second.rejected_reason == "approval_consumed"
    assert len(actuator.execute_action_calls) == 1


@pytest.mark.asyncio
async def test_approval_binds_to_proposal_hash():
    """Approval is granted for a specific params_hash. If the persisted
    remediation row is edited directly in the database after approval but
    before the tool actually runs, execute() must refuse — the approval no
    longer covers what is about to be sent to the Actuator."""
    remediation, repo, actuator, executor, approval_repo = _build()

    batch = await executor.preflight(
        [_call("tc1", "patrol_execute_remediation", {"remediation_id": remediation.id, "idempotency_key": "llm-supplied-key"})]
    )
    await executor.execute(batch)
    await approval_repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "approver-1")

    # Simulate an out-of-band DB edit between approval and execution.
    repo.remediations[remediation.id] = repo.remediations[remediation.id].model_copy(update={"params": {"replicas": 99}})

    result = await executor.resume(batch.batch_id, actor_id="approver-1")

    assert actuator.execute_action_calls == []
    assert result.calls[0].result.success is False
    stored = repo.remediations[remediation.id]
    assert stored.status == PatrolRemediationStatus.FAILED
    assert stored.error_code == "PARAMS_TAMPERED"


@pytest.mark.asyncio
async def test_capability_drift_rejects_execution():
    """The Actuator's *live* capability hash no longer matches the baseline
    that was persisted on the remediation record before approval (simulating
    TaskRunnerFactory's pre-approval preflight) -> refused, zero write calls.

    This is deliberately NOT "two back-to-back get_capabilities() calls
    compared against each other" — two calls made microseconds apart against
    the same live fake would always agree and could never catch a schema
    change that happened *during* the human review window. The baseline must
    come from outside the execute() call entirely, which is exactly what
    `capability_baseline=` here stands in for."""
    remediation, repo, actuator, executor, approval_repo = _build(capability_baseline="baseline-hash-a", live_hash="drifted-hash-b")

    batch = await executor.preflight(
        [_call("tc1", "patrol_execute_remediation", {"remediation_id": remediation.id, "idempotency_key": "llm-supplied-key"})]
    )
    await executor.execute(batch)
    await approval_repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "approver-1")

    result = await executor.resume(batch.batch_id, actor_id="approver-1")

    assert actuator.execute_action_calls == []
    assert actuator.get_capabilities_calls == 1
    assert result.calls[0].result.success is False
    stored = repo.remediations[remediation.id]
    assert stored.status == PatrolRemediationStatus.FAILED
    assert stored.error_code == "CAPABILITY_DRIFT"
    # The historical baseline itself must be left untouched (audit trail) —
    # only error_message records what was actually observed at drift time.
    assert stored.actuator_capability_hash == "baseline-hash-a"
    assert "drifted-hash-b" in (stored.error_message or "")


@pytest.mark.asyncio
async def test_capability_baseline_missing_rejects_execution():
    """No pre-approval baseline was ever persisted on the remediation record
    (e.g. code path predating the preflight, or data corruption) -> execute()
    must fail closed rather than trust whatever the Actuator reports right
    now. Zero Actuator calls at all — not even a live read, since there is
    nothing to compare it against."""
    remediation, repo, actuator, executor, approval_repo = _build(capability_baseline=None)

    batch = await executor.preflight(
        [_call("tc1", "patrol_execute_remediation", {"remediation_id": remediation.id, "idempotency_key": "llm-supplied-key"})]
    )
    await executor.execute(batch)
    await approval_repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "approver-1")

    result = await executor.resume(batch.batch_id, actor_id="approver-1")

    assert actuator.get_capabilities_calls == 0
    assert actuator.execute_action_calls == []
    assert result.calls[0].result.success is False
    stored = repo.remediations[remediation.id]
    assert stored.status == PatrolRemediationStatus.FAILED
    assert stored.error_code == "CAPABILITY_BASELINE_MISSING"


def _route_dependencies(router) -> dict[str, set]:
    return {
        f"{next(iter(route.methods))}:{route.path}": {dependency.call for dependency in route.dependant.dependencies}
        for route in router.routes
        if hasattr(route, "dependant") and hasattr(route, "methods")
    }


def test_auditor_cannot_create_or_approve():
    """AUDITOR 全拒: neither the propose route nor the generic tool-approval
    decision route (which governs approving a remediation's
    patrol_execute_remediation call, exactly like any other gated tool call)
    may be reached by an AUDITOR principal."""
    patrol_routes = _route_dependencies(patrol_router)
    assert require_non_auditor in patrol_routes["POST:/patrol-findings/{finding_id}/remediations"]

    session_routes = _route_dependencies(session_router)
    assert require_non_auditor in session_routes["POST:/sessions/{session_id}/tool-approval-batches/{batch_id}/decision"]
