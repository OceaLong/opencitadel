#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import hashlib
import hmac
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.services.artifact_service import ArtifactService, sanitize_html_for_preview
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.models.artifact import Artifact
from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope
from app.domain.utils.hitl import parse_gate_action, tool_matches_risk_list
from app.domain.utils.schedule_utils import compute_next_run, render_prompt_template
from app.infrastructure.external.scheduler.job_scheduler import try_become_scheduler_leader


def test_tool_matches_risk_list_wildcard():
    assert tool_matches_risk_list("mcp_slack_post", ["mcp_*"])
    assert not tool_matches_risk_list("read_file", ["write_file"])


def test_parse_gate_action():
    assert parse_gate_action("approve")[0] == "approve"
    assert parse_gate_action("reject: too risky")[0] == "reject"
    assert parse_gate_action("approve_with_edits")[0] == "approve_with_edits"
    assert parse_gate_action("takeover")[0] == "takeover"
    assert parse_gate_action("skip")[0] == "skip"
    assert parse_gate_action("maybe later")[0] == "unknown"
    assert parse_gate_action("")[0] == "unknown"


def test_sanitize_html_for_preview():
    raw = '<div onclick="alert(1)">Hi<script>alert("x")</script></div>'
    cleaned = sanitize_html_for_preview(raw)
    assert "<script" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()


def test_compute_next_run_interval():
    nxt = compute_next_run("interval", "120")
    assert nxt is not None


def test_render_prompt_template():
    out = render_prompt_template("Hello {{payload.name}}", {"name": "World"})
    assert "World" in out


def test_webhook_signature_and_idempotency_key():
    secret = "test-secret"
    body = b'{"event":"ping"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert ScheduledJobService.verify_webhook_signature(secret, body, signature)
    assert not ScheduledJobService.verify_webhook_signature(secret, body, "bad")
    token = "abc123"
    expected_key = f"webhook:idem:{token}:{hashlib.sha256(body).hexdigest()}"
    assert expected_key.startswith("webhook:idem:abc123:")


def test_artifact_write_uploads_to_object_storage():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=None)
    uow.artifact.save = AsyncMock()
    uow.commit = AsyncMock()

    object_storage = AsyncMock()
    object_storage.put_bytes = AsyncMock()

    def factory():
        return uow

    service = ArtifactService(factory, object_storage=object_storage)

    async def _run():
        artifact, event = await service.write_content(
            session_id="s1",
            artifact_id=None,
            kind="doc",
            title="Report",
            content="# Hello",
        )
        assert artifact.session_id == "s1"
        assert event.kind == "doc"
        object_storage.put_bytes.assert_awaited_once()

    asyncio.run(_run())


def _artifact_service_without_storage(uow):
    return ArtifactService(lambda: uow, object_storage=AsyncMock())


def test_artifact_scope_denied_without_session_access():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.get_metadata = AsyncMock(return_value=None)
    uow.artifact.list_by_session = AsyncMock(return_value=[])

    service = _artifact_service_without_storage(uow)
    scope = OwnerScope.personal("user-a")

    async def _run():
        with pytest.raises(PermissionError):
            await service.list_by_session("session-1", scope=scope)

    asyncio.run(_run())


def test_artifact_get_by_id_requires_scope():
    artifact = Artifact(id="a1", session_id="s1", kind="doc", title="T")
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=artifact)
    uow.session.get_metadata = AsyncMock(return_value=None)

    service = _artifact_service_without_storage(uow)

    async def _run():
        result = await service.get_by_id("a1", scope=OwnerScope.personal("other"))
        assert result is None

    asyncio.run(_run())


def test_scheduler_leader_only_renews_own_lease():
    redis = MagicMock()
    redis.client.set = AsyncMock(return_value=False)
    redis.client.get = AsyncMock(return_value=b"other-worker")
    redis.client.expire = AsyncMock(return_value=True)

    async def _run():
        with patch("app.infrastructure.external.scheduler.job_scheduler.get_redis", return_value=redis):
            assert await try_become_scheduler_leader(30) is False
        redis.client.expire.assert_not_awaited()

    asyncio.run(_run())


def test_scheduler_leader_renews_when_owner():
    redis = MagicMock()
    worker_id = "test-worker-1"
    redis.client.set = AsyncMock(side_effect=[True, False])
    redis.client.get = AsyncMock(return_value=worker_id.encode())
    redis.client.expire = AsyncMock(return_value=True)

    async def _run():
        with patch(
            "app.infrastructure.external.scheduler.job_scheduler._WORKER_ID",
            worker_id,
        ):
            with patch("app.infrastructure.external.scheduler.job_scheduler.get_redis", return_value=redis):
                assert await try_become_scheduler_leader(30) is True
                assert await try_become_scheduler_leader(30) is True
        redis.client.expire.assert_awaited_once()

    asyncio.run(_run())


def test_webhook_trigger_requires_signature():
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        name="hook",
        owner_user_id="u1",
        trigger_type="webhook",
        trigger_spec="",
        prompt_template="run",
        webhook_token="tok",
        webhook_secret_hash="legacy-sha256-only",
        enabled=True,
    )
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.scheduled_job.get_by_webhook_token = AsyncMock(return_value=job)

    service = ScheduledJobService(lambda: uow)

    async def _run():
        session_id, error = await service.trigger_webhook("tok", b"{}", "", {})
        assert session_id is None
        assert error == "unauthorized"

    asyncio.run(_run())
