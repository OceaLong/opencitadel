# ruff: noqa: F401,F811 -- real owned DB fixture graph
"""C02: native OpenAI quota failure then smaller Anthropic context, same preparation."""

import pytest
from sqlalchemy import text

from app.domain.execution.context_models import ContextCompilation
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


@pytest.mark.parametrize(
    "budget_binding_fixture", [{"smaller_context_fallback": True}], indirect=True
)
@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "code_definition": True,
            "iterations": 1,
            "session_messages": 1,
            "fallback_before_first_chunk": True,
            "fallback_summary": summary,
            "provided_history": [
                {
                    "role": "user",
                    "content": f"Archive {index}: amount=7341; do not renew. "
                    + "Routine evidence. " * 100,
                }
                for index in range(48)
            ],
        }
        for summary in (False, True)
    ],
    indirect=True,
    ids=["main-fallback", "summary-fallback"],
)
async def test_c02_smaller_native_fallback_consumes_same_compacted_manifest(compact_runtime):
    r = compact_runtime
    for _ in range(100):
        await r.kernel.run_projector_once(r.scope, limit=100)
        decisions = await r.kernel.run_decisions_once(limit=1)
        activities = await r.kernel.run_activities_once(limit=1)
        assert not decisions.errors
        assert not activities.failed
        assert not activities.unknown
        if not decisions.submitted and not activities.claimed:
            break
    async with r.uow(r.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                r.binding.run_id, owner_scope=(r.scope.user_id, None)
            )
        ).replay.state
        assert state.status.value == "completed"
        assert await work.db_session.scalar(
            text("SELECT count(*) FROM execution_model_dispatches")
        ) == len(r.sends)
    assert r.responses.summary
    assert len(r.responses.main) == 1
    summary_fallback = r.sends[-1]["model"] == "gpt-4.1-2025-04-14"
    pairs = (
        list(zip(r.sends[:-1:2], r.sends[1:-1:2], strict=True))
        if summary_fallback
        else [r.sends[-2:]]
    )
    assert pairs
    for primary, fallback in pairs:
        assert [primary["model"], fallback["model"]] == [
            "gpt-4.1-2025-04-14",
            "claude-haiku-4-5-20251001",
        ]
        assert (
            primary["max_completion_tokens"]
            == fallback["max_tokens"]
            == (1024 if summary_fallback else 4096)
        )
        # Native Anthropic lifts system out; evidence remains the same frozen text.
        assert primary["messages"][-1]["content"] == fallback["messages"][-1]["content"]
    prepare_ids = {
        identity for identity, kind, _ in state.requested_activities if kind == "context.prepare"
    }
    prepared = next(
        ref
        for identity, _, ref, *_rest in reversed(state.activity_results)
        if identity in prepare_ids
    )
    compilation = ContextCompilation.model_validate(
        (await r.objects.load_context_document(prepared))["compilation"]
    )
    assert len(compilation.manifest.candidates) == 2
    assert compilation.manifest.candidates[0].window > compilation.manifest.candidates[1].window
    assert compilation.manifest.estimated_tokens <= compilation.manifest.input_budget
    assert len(r.sends) == (
        2 * len(r.responses.summary) + 1 if summary_fallback else len(r.responses.summary) + 2
    )
