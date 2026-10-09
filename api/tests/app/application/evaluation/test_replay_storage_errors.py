"""Production SDK errors traverse storage adapters and both replay read boundaries."""

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from minio.error import S3Error
from qcloud_cos.cos_exception import CosServiceError

from app.application.evaluation.replay_adapter import ReplayAdapter
from app.application.evaluation.replay_runtime import ReplayRuntime
from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.decisions.base import fail_for_activity
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import (
    MatchRule,
    RecordedContract,
    RecordingSlot,
    recording_key,
)
from app.domain.execution.run import RunState
from app.domain.services.tools.capability_policy import READ_SAFE
from app.infrastructure.adapters.object_storage import create_object_storage_adapter
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio
from tests.app.application.execution.test_activity_worker import (
    NOW,
    FakeRunContexts,
    FakeRunService,
    FakeStore,
    claim,
)


def storage_error(provider, kind):
    if kind == "transport":
        return OSError("connection interrupted")
    code, status = {
        "missing": ("NoSuchKey", 404),
        "bucket": ("NoSuchBucket", 404),
        "denied": ("AccessDenied", 403),
        "auth": ("SignatureDoesNotMatch", 403),
        "service": ("InternalError", 500),
    }[kind]
    if provider == "minio":
        return S3Error(None, code, "provider error", "/object", "request", "host")
    return CosServiceError("GET", {"code": code, "message": "provider error"}, status)


def replay_reader(provider, kind, boundary):
    error = storage_error(provider, kind)

    class Client:
        def get_object(self, *args, **kwargs):
            raise error

    settings = SimpleNamespace(minio_bucket="test", cos_bucket="test")
    raw = Minio(settings) if provider == "minio" else Cos(settings)
    raw._client = Client()
    objects = create_object_storage_adapter(provider=provider, client=raw)
    contract = RecordedContract(
        name="read",
        pack="test",
        schema_body={"function": {"parameters": {"type": "object"}}},
        policy=READ_SAFE,
        binding_revision="1",
        authority_revision="1",
    )
    version = uuid4()
    slot = RecordingSlot(
        id=uuid4(),
        tool="read",
        contract_digest=contract.digest,
        match_key=recording_key("read", contract.digest, {}, "root", 0),
        rule=MatchRule(),
        branch="root",
        ordinal=0,
        object_id=uuid4(),
        result_digest=hashlib.sha256(b"{}").hexdigest(),
        result_bytes=2,
        simulated_effect=False,
    )

    class Repo:
        async def lock_call(self, *args):
            pass

        async def consumed(self, *args):
            return (
                {"slot_id": slot.id, "match_key": slot.match_key, "version_id": version}
                if boundary == "runtime"
                else None
            )

        async def object(self, *args):
            return {"storage_key": "fixed", "digest": slot.result_digest, "size_bytes": 2}

        async def consume(self, *args):
            pytest.fail("missing object consumed")

    class Authority:
        @asynccontextmanager
        async def open(self, context):
            yield SimpleNamespace(
                scope="scope",
                manifest=SimpleNamespace(
                    id=version, revision=1, contracts=(contract,), slots=(slot,)
                ),
                repo=Repo(),
                uow=self,
            )

        async def approve(self, *args):
            pass

        async def commit(self):
            pytest.fail("missing object committed")

    async def read(context):
        if boundary == "adapter":
            return await ReplayAdapter(Authority(), objects).match(
                context, "read", contract.digest, {}, "root", 0
            )
        return await ReplayRuntime(Authority(), objects, None)._read(
            context, contract, SimpleNamespace(result_ref="fixed")
        )

    return read, error


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minio", "cos"])
@pytest.mark.parametrize("boundary", ["adapter", "runtime"])
@pytest.mark.parametrize("kind", ["missing", "transport"])
async def test_sdk_missing_object_is_fatal_and_transport_retains_worker_backoff(
    provider, boundary, kind
):
    read, _ = replay_reader(provider, kind, boundary)

    class Handler:
        activity_type = "model.call"
        idempotent = True

        async def execute(self, request, context):
            return await read(context)

    registry = ActivityRegistry()
    registry.register(Handler())
    candidate = claim()
    service = FakeRunService()
    worker = ActivityWorker(
        store=FakeStore((candidate,)),
        run_service=service,
        run_contexts=FakeRunContexts(),
        registry=registry,
        worker_id="storage-replay",
    )
    stats = await worker.run_once(now=NOW, limit=1)
    if kind == "transport":
        assert stats.deferred == 1
        assert stats.failed == 0
        assert not any(command.command_type == "FailActivity" for command, _ in service.commands)
        return
    assert stats.failed == 1
    assert stats.deferred == 0
    failure = service.commands[-1][0]
    assert failure.payload["failure_code"] == ReplayMismatch.code
    state = RunState(
        run_id=uuid4(),
        activity_failure_codes=((candidate.request.activity_id, 0, ReplayMismatch.code),),
    )
    assert (
        fail_for_activity(
            state, "failed", activity_id=candidate.request.activity_id, max_retries=5
        ).payload["retryable"]
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minio", "cos"])
@pytest.mark.parametrize("boundary", ["adapter", "runtime"])
@pytest.mark.parametrize("kind", ["transport", "bucket", "denied", "auth", "service"])
async def test_non_object_missing_errors_preserve_original_classification(provider, boundary, kind):
    read, error = replay_reader(provider, kind, boundary)
    context = SimpleNamespace(activity_id=uuid4(), run=SimpleNamespace(run_id=uuid4()))
    with pytest.raises(type(error)) as raised:
        await read(context)
    assert raised.value is error
