"""Live Operations Policy must stop an admitted remediation before effect."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.application.execution.activities.remediation import RemediationActivityHandler
from app.domain.execution.activity import ActivityContext, ActivityRequest
from app.domain.runtime_policy import (
    OperationsPolicy,
    PatrolOperationsPolicy,
    PatrolRemediationMode,
)
from tests.app.execution_test_support import run_execution_context_for
from tests.runtime_policy_support import MutablePolicyReader


class _Objects:
    async def load_input(self, *, key: str, expected_digest: str) -> dict:
        return {"remediation_id": "rem-1", "session_id": "session-1"}

    async def put_result(self, activity_id: UUID, payload: dict) -> str:
        return "result://remediation"


@pytest.mark.asyncio
async def test_current_disabled_policy_denies_before_remediation_executor() -> None:
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            patrol=PatrolOperationsPolicy(remediation=PatrolRemediationMode.DISABLED),
        )
    )
    executor = AsyncMock()
    handler = RemediationActivityHandler(
        objects=_Objects(),
        executor=executor,
        policy_reader=reader,
    )
    request = ActivityRequest(
        activity_id=UUID("70000000-0000-0000-0000-000000000009"),
        activity_type="remediation.execute",
        aggregate_type="run",
        aggregate_id="80000000-0000-0000-0000-000000000009",
        generation=0,
        timeout_at=datetime(2026, 8, 26, tzinfo=UTC),
        input_ref="input://remediation",
        input_digest="a" * 64,
    )
    context = ActivityContext(
        worker_id="worker-1",
        claim_generation=1,
        idempotency_key="activity-1",
        owner_user_id="user-1",
        team_id=None,
        run=run_execution_context_for("remediation"),
    )

    outcome = await handler.execute(request, context)

    assert outcome.status == "failed"
    assert outcome.failure_code == "POLICY_DENIED"
    executor.execute.assert_not_awaited()
    assert reader.operations_calls[0][0] is True
