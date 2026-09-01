import asyncio
import hashlib
import hmac
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.services.artifact_service import (
    ArtifactService,
    _decode_text_content,
    _rank_session_files_for_artifact,
    sanitize_html_for_preview,
)
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.models.artifact import Artifact
from app.domain.models.file import File
from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import OperationsPolicy, SchedulerPolicy
from app.domain.utils.schedule_utils import compute_next_run, render_prompt_template
from app.infrastructure.adapters.redis_capabilities import RedisLeaseManager
from app.infrastructure.external.scheduler.job_scheduler import try_become_scheduler_leader
from tests.runtime_policy_support import MutablePolicyReader

_SECRET_CIPHER = SimpleNamespace(
    current_key_id="test",
    encrypt_versioned=lambda value: f"encrypted:{value}",
    decrypt_versioned=lambda value: value.removeprefix("encrypted:"),
)


def test_sanitize_html_for_preview():
    raw = '<div onclick="alert(1)">Hi<script>alert("x")</script></div>'
    cleaned = sanitize_html_for_preview(raw)
    assert "<script" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()


def test_decode_text_content_valid_utf8():
    text, incomplete = _decode_text_content("你好 world".encode())
    assert text == "你好 world"
    assert incomplete is False


def test_decode_text_content_truncated_utf8():
    truncated = "你好".encode()[:-1]
    text, incomplete = _decode_text_content(truncated)
    assert incomplete is True
    assert "\ufffd" in text


def test_get_content_text_returns_incomplete_for_corrupt_storage():
    artifact = Artifact(
        id="a1",
        session_id="s1",
        kind="doc",
        title="Report",
        storage_ref="artifacts/s1/a1/v1.md",
        version_refs=["artifacts/s1/a1/v1.md"],
    )
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=artifact)
    uow.session.get_files = AsyncMock(return_value=[])

    object_storage = AsyncMock()
    object_storage.get_bytes = AsyncMock(return_value="partial 中文".encode()[:-1])

    service = ArtifactService(lambda: uow, object_storage=object_storage)

    async def _run():
        text, incomplete = await service.get_content_text("a1", auto_repair=False)
        assert incomplete is True
        assert "partial" in text

    asyncio.run(_run())


def test_get_content_text_recovers_from_session_attachment():
    artifact = Artifact(
        id="a1",
        session_id="s1",
        kind="doc",
        title="final_road_trip_guide.md",
        storage_ref="artifacts/s1/a1/v1.md",
        version_refs=["artifacts/s1/a1/v1.md"],
    )
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=artifact)
    uow.artifact.save = AsyncMock()
    uow.commit = AsyncMock()
    uow.session.get_files = AsyncMock(
        return_value=[
            File(
                id="file-1",
                filename="final_road_trip_guide.md",
                filepath="/home/ubuntu/final_road_trip_guide.md",
                size=12000,
            ),
        ]
    )

    stored: dict[str, bytes] = {
        "artifacts/s1/a1/v1.md": "partial 中文".encode()[:-1],
    }

    async def put_bytes(key: str, data: bytes) -> None:
        stored[key] = data

    async def get_bytes(key: str) -> bytes:
        return stored[key]

    object_storage = AsyncMock()
    object_storage.put_bytes = AsyncMock(side_effect=put_bytes)
    object_storage.get_bytes = AsyncMock(side_effect=get_bytes)

    file_storage = AsyncMock()
    recovered = "# 完整成都自驾游指南\n\n气温在二十二度至三十一度。"
    file_storage.download_file = AsyncMock(
        return_value=(MagicMock(read=lambda: recovered.encode("utf-8")), File(id="file-1"))
    )

    service = ArtifactService(lambda: uow, object_storage=object_storage, file_storage=file_storage)

    async def _run():
        text, incomplete = await service.get_content_text("a1")
        assert incomplete is False
        assert "完整成都自驾游指南" in text
        assert any(key.endswith("v2.md") for key in stored)

    asyncio.run(_run())


def test_write_content_rejects_upload_mismatch():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=None)
    uow.artifact.save = AsyncMock()
    uow.commit = AsyncMock()

    object_storage = AsyncMock()
    object_storage.put_bytes = AsyncMock()
    object_storage.get_bytes = AsyncMock(return_value=b"short")

    service = ArtifactService(lambda: uow, object_storage=object_storage)

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    async def _run():
        with (
            patch(
                "app.application.services.artifact_service.asyncio.sleep",
                new=_immediate_sleep,
            ),
            pytest.raises(ValueError, match="上传校验失败"),
        ):
            await service.write_content(
                session_id="s1",
                artifact_id=None,
                kind="doc",
                title="Report",
                content="# Hello",
            )

    asyncio.run(_run())


def test_write_content_retries_verify_until_match():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=None)
    uow.artifact.save = AsyncMock()
    uow.commit = AsyncMock()

    expected = b"# Hello"
    responses = [b"short", expected]
    object_storage = AsyncMock()
    object_storage.put_bytes = AsyncMock()

    async def get_bytes(_key: str) -> bytes:
        return responses.pop(0)

    object_storage.get_bytes = AsyncMock(side_effect=get_bytes)

    service = ArtifactService(lambda: uow, object_storage=object_storage)

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    async def _run():
        with patch("app.application.services.artifact_service.asyncio.sleep", new=_immediate_sleep):
            artifact = await service.write_content(
                session_id="s1",
                artifact_id=None,
                kind="doc",
                title="Report",
                content="# Hello",
            )
        assert artifact.title == "Report"
        assert object_storage.get_bytes.await_count == 2

    asyncio.run(_run())


def test_rank_session_files_for_artifact_prefers_exact_title():
    artifact = Artifact(id="a1", session_id="s1", kind="doc", title="report.md")
    files = [
        File(id="1", filename="notes.md", filepath="/home/ubuntu/notes.md", size=100),
        File(id="2", filename="report.md", filepath="/home/ubuntu/report.md", size=200),
    ]
    ranked = _rank_session_files_for_artifact(artifact, files)
    assert ranked[0].filename == "report.md"


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
    stored: dict[str, bytes] = {}

    async def put_bytes(key: str, data: bytes) -> None:
        stored[key] = data

    async def get_bytes(key: str) -> bytes:
        return stored[key]

    object_storage.put_bytes = AsyncMock(side_effect=put_bytes)
    object_storage.get_bytes = AsyncMock(side_effect=get_bytes)

    def factory():
        return uow

    service = ArtifactService(factory, object_storage=object_storage)

    async def _run():
        artifact = await service.write_content(
            session_id="s1",
            artifact_id=None,
            kind="doc",
            title="Report",
            content="# Hello",
        )
        assert artifact.session_id == "s1"
        assert artifact.kind == "doc"
        object_storage.put_bytes.assert_awaited_once()
        object_storage.get_bytes.assert_awaited()

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


def test_artifact_revoke_share_clears_token_for_owner():
    artifact = Artifact(
        id="a1", session_id="s1", kind="doc", title="T", share_token="tok", share_expires_at=None
    )
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=artifact)
    uow.session.get_metadata = AsyncMock(return_value=object())
    uow.artifact.save = AsyncMock()
    uow.commit = AsyncMock()

    service = _artifact_service_without_storage(uow)

    async def _run():
        await service.revoke_share_link("a1", scope=OwnerScope.personal("owner"))
        assert artifact.share_token is None
        assert artifact.share_expires_at is None
        uow.artifact.save.assert_awaited_once()
        uow.commit.assert_awaited_once()

    asyncio.run(_run())


def test_artifact_revoke_share_denied_without_session_access():
    # Regression: revoke must enforce owner scope so a non-owner can't clear
    # another tenant's share token.
    artifact = Artifact(id="a1", session_id="s1", kind="doc", title="T", share_token="tok")
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.artifact.get_by_id = AsyncMock(return_value=artifact)
    uow.session.get_metadata = AsyncMock(return_value=None)
    uow.artifact.save = AsyncMock()

    service = _artifact_service_without_storage(uow)

    async def _run():
        with pytest.raises(PermissionError):
            await service.revoke_share_link("a1", scope=OwnerScope.personal("attacker"))
        assert artifact.share_token == "tok"
        uow.artifact.save.assert_not_awaited()

    asyncio.run(_run())


def test_artifact_tool_requires_content_or_source_path():
    from app.domain.services.tools.artifact import ArtifactTool

    tool = ArtifactTool(write_fn=AsyncMock(), finalize_fn=AsyncMock())

    async def _run():
        result = await tool.artifact_write(kind="doc", title="Report")
        assert result.success is False
        assert "content 或 source_path" in (result.message or "")

    asyncio.run(_run())


def test_artifact_tool_rejects_invalid_artifact_id():
    from app.domain.services.tools.artifact import ArtifactTool

    tool = ArtifactTool(write_fn=AsyncMock(), finalize_fn=AsyncMock())

    async def _run():
        result = await tool.artifact_write(
            kind="doc",
            title="Report",
            content="# Hello",
            artifact_id="temp",
        )
        assert result.success is False
        assert "无效的 artifact_id[temp]" in (result.message or "")
        assert "留空 artifact_id" in (result.message or "")

    asyncio.run(_run())


def test_artifact_tool_catches_storage_errors():
    from app.domain.services.tools.artifact import ArtifactTool

    async def failing_write(**_kwargs):
        raise OSError("object storage write failed")

    tool = ArtifactTool(write_fn=failing_write, finalize_fn=AsyncMock())

    async def _run():
        result = await tool.artifact_write(kind="doc", title="Report", content="# Hello")
        assert result.success is False
        assert "object storage write failed" in (result.message or "")

    asyncio.run(_run())


def test_artifact_finalize_not_found_returns_failure():
    from app.domain.services.tools.artifact import ArtifactTool

    async def failing_finalize(_artifact_id: str):
        raise ValueError(
            "交付物[00000000-0000-0000-0000-000000000099]不存在。"
            "若为新交付物请将 artifact_id 留空；若为更新请使用 artifact_write 返回的 id。"
        )

    tool = ArtifactTool(write_fn=AsyncMock(), finalize_fn=failing_finalize)

    async def _run():
        result = await tool.artifact_finalize("00000000-0000-0000-0000-000000000099")
        assert result.success is False
        assert "不存在" in (result.message or "")

    asyncio.run(_run())


def test_scheduler_leader_only_renews_own_lease():
    redis = MagicMock()
    redis.client.set = AsyncMock(return_value=False)
    redis.client.eval = AsyncMock(return_value=0)
    leases = RedisLeaseManager(redis.client)

    async def _run():
        assert (
            await try_become_scheduler_leader(
                leases,
                worker_id="test-worker-1",
                lease_seconds=30,
            )
            is False
        )
        redis.client.eval.assert_awaited_once()
        script, key_count, key, owner, ttl_ms = redis.client.eval.await_args.args
        assert "opencitadel:renew-lease" in script
        assert (key_count, key, ttl_ms) == (1, "scheduler:leader", 30_000)
        assert owner

    asyncio.run(_run())


def test_scheduler_leader_renews_when_owner():
    redis = MagicMock()
    worker_id = "test-worker-1"
    redis.client.set = AsyncMock(side_effect=[True, False])
    redis.client.eval = AsyncMock(return_value=1)
    leases = RedisLeaseManager(redis.client)

    async def _run():
        assert (
            await try_become_scheduler_leader(
                leases,
                worker_id=worker_id,
                lease_seconds=30,
            )
            is True
        )
        assert (
            await try_become_scheduler_leader(
                leases,
                worker_id=worker_id,
                lease_seconds=30,
            )
            is True
        )
        redis.client.eval.assert_awaited_once()
        script, key_count, key, owner, ttl_ms = redis.client.eval.await_args.args
        assert "opencitadel:renew-lease" in script
        assert (key_count, key, owner, ttl_ms) == (
            1,
            "scheduler:leader",
            worker_id,
            30_000,
        )

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

    service = ScheduledJobService(
        lambda: uow,
        patrol_run_service=SimpleNamespace(),
        resource_guard=SimpleNamespace(),
        resource_binding_service=SimpleNamespace(),
        run_admission_service=SimpleNamespace(),
        run_projection=SimpleNamespace(),
        policy_reader=MutablePolicyReader(),
        notification_service=SimpleNamespace(),
        secret_cipher=_SECRET_CIPHER,
    )

    async def _run():
        session_id, error = await service.trigger_webhook("tok", b"{}", "", {})
        assert session_id is None
        assert error == "unauthorized"

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_webhook_acceptance_uses_current_policy_ttl() -> None:
    secret = "webhook-secret"
    body = b'{"event":"deploy"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        name="hook",
        owner_user_id="u1",
        trigger_type="webhook",
        trigger_spec="",
        prompt_template="run",
        webhook_token="tok",
        webhook_secret_hash="encrypted",
        enabled=True,
    )
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.scheduled_job.get_by_webhook_token = AsyncMock(return_value=job)
    reader = MutablePolicyReader(
        operations=OperationsPolicy(scheduler=SchedulerPolicy(webhook_idempotency_ttl_seconds=60))
    )
    service = ScheduledJobService(
        lambda: uow,
        patrol_run_service=SimpleNamespace(),
        resource_guard=SimpleNamespace(),
        resource_binding_service=SimpleNamespace(),
        run_admission_service=SimpleNamespace(),
        run_projection=SimpleNamespace(),
        policy_reader=reader,
        notification_service=SimpleNamespace(),
        secret_cipher=_SECRET_CIPHER,
    )
    service._decrypt_webhook_secret = lambda _stored: secret
    service.trigger_job = AsyncMock(return_value="session-1")

    assert await service.trigger_webhook("tok", body, signature, {}) == ("session-1", None)
    first_firing = service.trigger_job.await_args.kwargs["firing_id"]
    reader.set_operations(
        OperationsPolicy(scheduler=SchedulerPolicy(webhook_idempotency_ttl_seconds=120))
    )
    assert await service.trigger_webhook("tok", body, signature, {}) == ("session-1", None)
    second_firing = service.trigger_job.await_args.kwargs["firing_id"]

    assert first_firing != second_firing
    assert [fresh for fresh, _now in reader.operations_calls] == [True, True]


@pytest.mark.asyncio
async def test_scheduler_tightening_rolls_back_before_run_admission() -> None:
    enabled = MutablePolicyReader(
        operations=OperationsPolicy(scheduler=SchedulerPolicy(enabled=True))
    ).operations
    disabled = MutablePolicyReader(
        operations=OperationsPolicy(scheduler=SchedulerPolicy(enabled=False))
    ).operations

    class _TighteningReader:
        def __init__(self):
            self.values = [enabled, disabled]
            self.calls = []

        async def active_operations(self, *, require_fresh, now):
            self.calls.append((require_fresh, now))
            return self.values.pop(0)

    class _Uow:
        def __init__(self, job):
            self.exit_error = None
            self.scheduled_job = SimpleNamespace(
                get_by_id=AsyncMock(return_value=job),
                save=AsyncMock(),
            )
            self.session = SimpleNamespace(save=AsyncMock())
            empty_repo = SimpleNamespace(get_by_id=AsyncMock())
            self.inference_model = empty_repo
            self.skill = empty_repo
            self.knowledge_base = SimpleNamespace(get_kb=AsyncMock())
            self.execution_commands = object()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, _exc, _tb):
            self.exit_error = exc_type
            return False

    job = ScheduledJob(
        id=str(uuid.uuid4()),
        name="interval",
        owner_user_id="u1",
        trigger_type="interval",
        trigger_spec="3600",
        prompt_template="run",
        enabled=True,
    )
    uow = _Uow(job)
    admission = SimpleNamespace(admit=AsyncMock(return_value=uuid.uuid4()))
    reader = _TighteningReader()
    service = ScheduledJobService(
        lambda: uow,
        patrol_run_service=SimpleNamespace(),
        resource_guard=SimpleNamespace(),
        resource_binding_service=SimpleNamespace(),
        run_admission_service=admission,
        run_projection=SimpleNamespace(),
        policy_reader=reader,
        notification_service=SimpleNamespace(),
        secret_cipher=_SECRET_CIPHER,
    )

    result = await service.trigger_job(job)

    assert result is None
    admission.admit.assert_not_awaited()
    assert uow.exit_error is not None
    assert [fresh for fresh, _now in reader.calls] == [True, True]
