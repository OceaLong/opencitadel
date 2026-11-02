# ruff: noqa: F401,F811 -- owned PostgreSQL fixture graph
"""A02/A03: approved HTTP write, worker restart, compaction and exact late read."""

import json

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.application.execution.effect_service import EffectService
from app.composition.kernel_runtime import build_execution_kernel_runtime
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import EXTERNAL_WRITE
from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


class WriteOrder(BaseTool):
    name = "owned_order"

    def __init__(self, client):
        super().__init__()
        self.client = client

    @tool(
        name="write_order",
        description="Write the approved fixed order once",
        parameters={key: {"type": "string"} for key in ("order", "tax_id", "amount")},
        required=["order", "tax_id", "amount"],
        policy=EXTERNAL_WRITE,
    )
    async def write_order(self, order, tax_id, amount):
        response = await self.client.post(
            "/orders", json={"order": order, "tax_id": tax_id, "amount": amount}
        )
        response.raise_for_status()
        return ToolResult(success=True, data=response.json())


@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 12,
            "read_early_tool": True,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
            "message": "Write ORD83 once: tax_id=CN-8841, amount=17329.47. Never change or repeat it.",
        }
    ],
    indirect=True,
)
async def test_a02_approved_write_survives_restart_and_compaction_without_resend(compact_runtime):
    r = compact_runtime
    writes = []

    async def remote(request):
        assert request.method == "POST"
        assert request.url.path == "/orders"
        body = json.loads(request.content)
        assert body == {"order": "ORD83", "tax_id": "CN-8841", "amount": "17329.47"}
        writes.append(body)
        return httpx.Response(200, json={**body, "receipt": "R-1931"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(remote), base_url="https://owned-write.invalid"
    ) as client:
        pack = WriteOrder(client)
        build = r.catalog._build

        async def with_write(payload, context):
            built = await build(payload, context)
            built.packs.append(pack)
            return built

        r.catalog._build = with_write

        def kernel_uow():
            work = r.uow(r.auth)
            # Effects share the orchestrator's signed session contract within
            # the caller-owned transaction, as in the production composition.
            work.session_factory = r.sessions
            return work

        effects = EffectService(
            store=PostgresEffectStore(
                session_factory=r.sessions,
                authorization=r.auth,
                orchestrator=r.orchestrator,
                uow_factory=kernel_uow,
            ),
            objects=r.objects,
            files=None,
        )
        r.registry.resolve("tool.call")._effects = effects
        message = r.responses.message

        def first_write(wire):
            result = message(wire)
            if wire["max_completion_tokens"] == 1024:
                summary = json.loads(result["content"])
                summary["facts"][0]["text"] = (
                    "Routine progress; consult the formal operation ledger."
                )
                result["content"] = json.dumps(summary)
            elif len(r.responses.main) == 1:
                result["tool_calls"][0]["function"] = {
                    "name": "write_order",
                    "arguments": json.dumps(
                        {"order": "ORD83", "tax_id": "CN-8841", "amount": "17329.47"}
                    ),
                }
            return result

        r.responses.message = first_write

        async def state():
            async with r.uow(r.auth) as work:
                return (
                    await PostgresVerifiedRunLoader(work.db_session).load(
                        r.binding.run_id, owner_scope=(r.scope.user_id, None)
                    )
                ).replay.state

        async def tick(kernel):
            await kernel.run_projector_once(r.scope, limit=100)
            decisions = await kernel.run_decisions_once(limit=1)
            assert not decisions.errors
            assert not decisions.rejected
            activities = await kernel.run_activities_once(limit=1)
            assert not activities.failed
            assert not activities.unknown
            assert not activities.stale

        for _ in range(20):
            await tick(r.kernel)
            waiting = await state()
            if waiting.pending_approval_id is not None:
                break
        assert waiting.pending_approval_id is not None
        assert writes == []
        async with r.api_uow() as work:
            subject_digest = await work.execution_usage.approval_subject(
                r.scope, r.binding.run_id, waiting.pending_approval_id
            )
        assert subject_digest == waiting.pending_approval_subject_digest
        await r.command(
            "DecideApproval",
            {
                "approval_id": str(waiting.pending_approval_id),
                "decision": "approved",
                "actor_user_id": r.principal.user_id,
                "expected_subject_digest": subject_digest,
            },
            version=2,
        )

        # A fresh process-equivalent runtime resumes the durable approval and
        # queue; no snapshot, event, outcome or lease is edited by the test.
        redis = Redis()
        restarted = build_execution_kernel_runtime(
            session_factory=r.sessions,
            redis=redis,
            authorization=r.auth,
            activity_registry=r.registry,
            definitions=r.definitions,
            worker_id="write-context-restarted",
        )
        try:
            for _ in range(220):
                await tick(restarted)
                current = await state()
                if current.status.value in {"completed", "failed", "cancelled"}:
                    break
        finally:
            await redis.aclose()
        assert current.status.value == "completed", current.activity_failure_codes
        assert len(writes) == 1
        assert len(current.effect_operations) == 1
        effect = current.effect_operations[0]
        assert effect.state == "applied"
        assert effect.spec.operation_id == effect.spec.invocation_id
        assert r.responses.summary
        final = r.responses.main[-1]
        assert not final.get("tools")
        evidence = "\n".join(item.get("content", "") for item in final["messages"])
        assert str(effect.spec.operation_id) in evidence
        assert effect.spec.contract_digest in evidence
        assert '"state":"applied"' in evidence
        late = next(item for item in final["messages"] if item.get("tool_call_id") == "read-11-1")
        original = json.loads(json.loads(late["content"])["data"]["text"])
        assert original["data"] == {**writes[0], "receipt": "R-1931"}
        assert all(item.get("tool_call_id") != "read-0-0" for item in final["messages"])
        async with r.uow(r.auth) as work:
            assert (
                await work.db_session.scalar(
                    text(
                        "SELECT count(*) FROM execution_events WHERE event_type='EffectDispatchStarted'"
                    )
                )
                == 1
            )
