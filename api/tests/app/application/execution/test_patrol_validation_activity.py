from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.application.execution.activities.patrol import PatrolValidationActivityHandler
from app.application.patrol_templates import load_patrol_template
from app.domain.execution.activity import ActivityContext, ActivityRequest
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.patrol import PatrolPack, PatrolPackStatus
from tests.app.execution_test_support import run_execution_context_for

VALIDATION_RUN_ID = UUID("70000000-0000-0000-0000-000000000010")


class _Objects:
    async def load_input(self, *, key: str, expected_digest: str) -> dict:
        return {
            "pack_id": "pack-1",
            "pack_version": 1,
            "validation_run_id": str(VALIDATION_RUN_ID),
            "actor_user_id": "user-1",
        }

    async def put_result(self, activity_id: UUID, payload: dict) -> str:
        assert payload == {
            "pack_id": "pack-1",
            "pack_version": 1,
            "status": "draft",
            "ok": True,
        }
        return "result://patrol-validation"


class _Uow:
    def __init__(self, server: MCPServerRecord) -> None:
        self.mcp_server = SimpleNamespace(get_by_id=AsyncMock(return_value=server))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_validation_activity_owns_live_collector_calls_and_publication() -> None:
    config = load_patrol_template("kubernetes-baseline-v1")
    pack = PatrolPack(
        id="pack-1",
        owner_user_id="user-1",
        name="Daily",
        slug="daily",
        status=PatrolPackStatus.VALIDATING,
        version=1,
        config=config,
        mcp_server_id="server-1",
        validation_run_id=str(VALIDATION_RUN_ID),
    )
    completed = pack.model_copy(
        update={
            "status": PatrolPackStatus.DRAFT,
            "validation_run_id": None,
            "last_validated_version": 1,
            "validation_summary": {"ok": True},
        }
    )
    packs = SimpleNamespace(
        get_pack=AsyncMock(return_value=pack),
        complete_validation=AsyncMock(return_value=completed),
    )
    capabilities = {
        "enabled_tools": ["get_capabilities"],
        "overall_capability_hash": "c" * 64,
    }
    collector = SimpleNamespace(
        get_capabilities=AsyncMock(return_value=capabilities),
        dry_run=AsyncMock(return_value={"ok": True, "probes": []}),
    )
    server = MCPServerRecord(
        id="server-1",
        name="collector",
        url="https://collector.example/mcp",
    )
    handler = PatrolValidationActivityHandler(
        objects=_Objects(),
        uow_factory=lambda: _Uow(server),
        collector=collector,
        packs=packs,
    )
    request = ActivityRequest(
        activity_id=UUID("71000000-0000-0000-0000-000000000010"),
        activity_type="patrol.validate",
        aggregate_type="run",
        aggregate_id=str(VALIDATION_RUN_ID),
        generation=0,
        timeout_at=datetime(2026, 8, 28, tzinfo=UTC),
        input_ref="input://patrol-validation",
        input_digest="a" * 64,
    )
    context = ActivityContext(
        worker_id="worker-1",
        claim_generation=1,
        idempotency_key="activity-1",
        owner_user_id="user-1",
        team_id=None,
        run=run_execution_context_for("patrol", run_id=VALIDATION_RUN_ID),
    )

    outcome = await handler.execute(request, context)

    assert outcome.status == "succeeded"
    assert outcome.result_ref == "result://patrol-validation"
    collector.get_capabilities.assert_awaited_once()
    collector.dry_run.assert_awaited_once()
    packs.complete_validation.assert_awaited_once_with(
        pack_id="pack-1",
        scope=context.run.owner_scope,
        actor_user_id="user-1",
        validation_run_id=str(VALIDATION_RUN_ID),
        validated_version=1,
        capabilities=capabilities,
        dry_run={"ok": True, "probes": []},
        errors=[],
    )
