# ruff: noqa: F401,F811 -- owned database fixture graph
"""Mixed image/text attachment input uses the real compiler, authority and native permit."""

import json

import pytest
from sqlalchemy import text

from app.domain.execution.context_models import ContextCompilation
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


@pytest.mark.parametrize("change", ["none", "delete", "revision", "tamper_image"])
@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 1,
            "capture_content": True,
            "attachment_evidence": True,
            "large_image": True,
            "message": "请找回附件中的 ORD83 exact amount 和 never renew 原始约束，并检查图片。",
        }
    ],
    indirect=True,
)
async def test_c01_mixed_attachments_preserve_native_images_exact_original_and_authority(
    compact_runtime, change
):
    r = compact_runtime
    target = None
    for _ in range(80):
        await r.kernel.run_projector_once(r.scope, limit=100)
        decision = await r.kernel.run_decisions_once(limit=1)
        assert not decision.errors
        async with r.uow(r.auth) as work:
            target = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT request_payload FROM execution_activity_tasks WHERE aggregate_id=:run AND status='pending' AND activity_type='model.call' LIMIT 1"
                        ),
                        {"run": str(r.binding.run_id)},
                    )
                )
                .mappings()
                .first()
            )
        if target:
            break
        result = await r.kernel.run_activities_once(limit=1)
        if result.failed:
            async with r.uow(r.auth) as work:
                failures = (
                    await work.db_session.execute(
                        text(
                            "SELECT activity_type,failure_code FROM execution_activity_tasks WHERE failure_code IS NOT NULL"
                        )
                    )
                ).all()
            pytest.fail(str(failures))
        assert not result.failed
        assert not result.unknown
    assert target
    body = await r.objects.load_context_document(target["request_payload"]["context_ref"])
    manifest = ContextCompilation.model_validate(body["compilation"]).manifest
    assert manifest.estimated_tokens <= manifest.input_budget
    assert manifest.estimated_tokens >= manifest.serialized_bytes + 4096
    assert any(
        "ORD83: exact amount 7341; never renew." in message.content for message in manifest.messages
    ), ([message.content[-240:] for message in manifest.messages], body.get("original_lookups"))
    assert sum(len(message.media_parts) for message in manifest.messages) == 1
    sent = len(r.sends)
    if change in {"delete", "revision"}:
        async with r.uow(r.auth) as work:
            await work.db_session.execute(
                text(
                    "UPDATE files SET content_available=false WHERE id='context-picture'"
                    if change == "delete"
                    else "UPDATE files SET filename='changed.png' WHERE id='context-picture'"
                ),
            )
            await work.commit()
    if change == "tamper_image":
        from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader

        async with r.uow(r.auth) as work:
            state = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    str(r.binding.run_id), owner_scope=(r.scope.user_id, None)
                )
            ).replay.state
        original = await r.objects.load_input(
            key=state.semantic_payload["input_ref"],
            expected_digest=state.semantic_payload["input_digest"],
        )
        image = next(
            item for item in original["_context_files"] if item["representation"] == "image"
        )
        r.objects._storage.objects[image["object_ref"]] = b'{"kind":"tampered"}'
    outcome = await r.kernel.run_activities_once(limit=1)
    if change == "none":
        assert outcome.succeeded == 1
        wire = r.sends[-1]
        assert len(r.sends) == sent + 1
        assert wire["messages"] == [message.wire() for message in manifest.messages]
        assert sum(isinstance(message["content"], list) for message in wire["messages"]) == 1
        assert max(read["read_bytes"] for read in r.preparation_reads) <= 8388608
    else:
        assert outcome.failed == 1
        assert len(r.sends) == sent


@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "capture_content": True,
            "attachment_evidence": True,
            "message": "保留图片和附件，执行两个只读证据工具后结束。",
        }
    ],
    indirect=True,
)
async def test_c01_agent_image_attachment_and_tool_schema_share_one_input_budget(compact_runtime):
    r = compact_runtime
    for _ in range(80):
        await r.kernel.run_projector_once(r.scope, limit=100)
        decisions = await r.kernel.run_decisions_once(limit=1)
        activities = await r.kernel.run_activities_once(limit=1)
        assert not decisions.errors
        assert not activities.failed
        assert not activities.unknown
        if not decisions.submitted and not activities.claimed:
            break
    assert len(r.responses.main) == 2
    assert r.responses.main[0]["tools"]
    assert not r.responses.main[-1].get("tools")
    assert len(r.pack.calls) == 2
    for wire in r.responses.main:
        assert sum(isinstance(message["content"], list) for message in wire["messages"]) == 1
    assert max(read["read_bytes"] for read in r.preparation_reads) <= 8388608
