"""Protocol validation only; real owned Docker enforcement has a separate gate."""

from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_broker_rejects_arbitrary_command_and_scope_generation_mismatch():
    from app.infrastructure.evaluation.broker_protocol import LifecycleRequest
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    version, lease, operation = inputs()
    payload = {
        "lease": lease.model_dump(mode="json"),
        "operation": operation.model_dump(mode="json"),
        "version": version.model_dump(mode="json"),
    }
    assert LifecycleRequest.model_validate(payload).lease.id == lease.id
    with pytest.raises(ValidationError):
        LifecycleRequest.model_validate({**payload, "command": ["run", "--privileged"]})
    with pytest.raises(ValidationError, match="operation_identity"):
        LifecycleRequest.model_validate(
            {**payload, "operation": {**payload["operation"], "lease_id": str(uuid4())}}
        )
    with pytest.raises(ValidationError, match="operation_identity"):
        LifecycleRequest.model_validate(
            {**payload, "operation": {**payload["operation"], "generation": 99}}
        )


@pytest.mark.asyncio
async def test_remote_adapter_has_no_command_proxy():
    from app.infrastructure.evaluation.broker_adapter import BrokerEnvironmentAdapter

    with pytest.raises(ValueError, match="command_proxy_forbidden"):
        await BrokerEnvironmentAdapter.forbidden_command("run", "--privileged")


@pytest.mark.asyncio
async def test_receipt_restart_replay_conflict_and_ambiguous_side_effect(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.infrastructure.evaluation import broker_service
    from app.infrastructure.evaluation.broker_protocol import LifecycleRequest
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    version, lease, operation = inputs()
    adapter = SimpleNamespace(
        prepare=AsyncMock(return_value={"resources": []}),
        reset=AsyncMock(),
        verify=AsyncMock(),
        cleanup=AsyncMock(),
    )
    registry = SimpleNamespace(resolve=lambda *args: adapter, credentials={}, targets={})
    monkeypatch.setattr(
        broker_service, "build_environment_registry", lambda *args, **kwargs: registry
    )
    settings = SimpleNamespace(
        sandbox_broker_token="test-secret",
        evaluation_broker_journal_path=str(tmp_path / "journal.sqlite"),
    )
    request = LifecycleRequest(lease=lease, operation=operation, version=version)
    first = broker_service.EvaluationBroker(settings)
    assert await first.lifecycle(request) == {"resources": []}
    restarted = broker_service.EvaluationBroker(settings)
    assert await restarted.lifecycle(request) == {"resources": []}
    assert adapter.prepare.await_count == 1
    changed = request.model_copy(update={"version": version.model_copy(update={"revision": 2})})
    with pytest.raises(ValueError, match=r"binding|conflict"):
        await restarted.lifecycle(changed)
    next_request = request.model_copy(
        update={"operation": operation.model_copy(update={"id": uuid4(), "phase": "reset"})}
    )
    adapter.reset.side_effect = TimeoutError("physical result unknown")
    with pytest.raises(TimeoutError):
        await restarted.lifecycle(next_request)
    with pytest.raises(ValueError, match="operation_unknown"):
        await broker_service.EvaluationBroker(settings).lifecycle(next_request)
    adapter.reset.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_binds_full_case_slot_and_spec_across_operations(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.infrastructure.evaluation import broker_service
    from app.infrastructure.evaluation.broker_protocol import LifecycleRequest
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    version, lease, operation = inputs()
    adapter = SimpleNamespace(
        prepare=AsyncMock(return_value={"resources": []}),
        reset=AsyncMock(),
        verify=AsyncMock(),
        cleanup=AsyncMock(),
    )
    monkeypatch.setattr(
        broker_service,
        "build_environment_registry",
        lambda *args, **kwargs: SimpleNamespace(
            resolve=lambda *args: adapter, targets={}, credentials={}
        ),
    )
    broker = broker_service.EvaluationBroker(
        SimpleNamespace(
            sandbox_broker_token="secret",
            evaluation_broker_journal_path=str(tmp_path / "journal.sqlite"),
        )
    )
    request = LifecycleRequest(lease=lease, operation=operation, version=version)
    await broker.lifecycle(request)
    changed = request.model_copy(
        update={
            "lease": lease.model_copy(
                update={"case_slot": lease.case_slot.model_copy(update={"case_id": uuid4()})}
            ),
            "operation": operation.model_copy(update={"id": uuid4(), "phase": "cleanup"}),
        }
    )
    with pytest.raises(ValueError, match="binding_conflict"):
        await broker.lifecycle(changed)
    adapter.cleanup.assert_not_awaited()


def test_runtime_proof_rejects_changed_slot_resources_and_scope(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.infrastructure.evaluation import broker_service
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    _, lease, _ = inputs()
    adapter = object()
    monkeypatch.setattr(
        broker_service,
        "build_environment_registry",
        lambda *args, **kwargs: SimpleNamespace(adapters={"test": adapter}),
    )
    broker = broker_service.EvaluationBroker(
        SimpleNamespace(
            sandbox_broker_token="secret",
            evaluation_broker_journal_path=str(tmp_path / "journal.sqlite"),
        )
    )
    versions = {"adapter_revision": "test"}
    versions["broker_proof"] = broker.signature(lease, versions, ())
    signed = lease.model_copy(update={"actual_versions": versions})
    assert broker.trusted_lease(signed) is adapter
    for changed in (
        signed.model_copy(update={"generation": 2}),
        signed.model_copy(update={"resources": ({"id": "foreign"},)}),
        signed.model_copy(
            update={"case_slot": lease.case_slot.model_copy(update={"case_id": uuid4()})}
        ),
        signed.model_copy(
            update={"case_slot": lease.case_slot.model_copy(update={"workspace": "user:foreign"})}
        ),
    ):
        with pytest.raises(ValueError, match="proof_invalid"):
            broker.trusted_lease(changed)


@pytest.mark.asyncio
async def test_two_broker_instances_never_duplicate_inflight_physical_operation(
    tmp_path, monkeypatch
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.infrastructure.evaluation import broker_service
    from app.infrastructure.evaluation.broker_protocol import LifecycleRequest
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    version, lease, operation = inputs()
    entered, release = asyncio.Event(), asyncio.Event()

    async def physical(*args):
        entered.set()
        await release.wait()
        return {"resources": []}

    adapter = SimpleNamespace(
        prepare=AsyncMock(side_effect=physical), reset=None, verify=None, cleanup=None
    )
    monkeypatch.setattr(
        broker_service,
        "build_environment_registry",
        lambda *args, **kwargs: SimpleNamespace(
            resolve=lambda *args: adapter, targets={}, credentials={}
        ),
    )
    settings = SimpleNamespace(
        sandbox_broker_token="secret",
        evaluation_broker_journal_path=str(tmp_path / "journal.sqlite"),
    )
    request = LifecycleRequest(lease=lease, operation=operation, version=version)
    task = asyncio.create_task(broker_service.EvaluationBroker(settings).lifecycle(request))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(ValueError, match="operation_unknown"):
            await broker_service.EvaluationBroker(settings).lifecycle(request)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ValueError, match="operation_unknown"):
            await broker_service.EvaluationBroker(settings).lifecycle(request)
        adapter.prepare.assert_awaited_once()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_server_owned_acceptance_labels_do_not_change_default_adapter():
    from app.infrastructure.adapters.evaluation_environment import DockerEnvironmentAdapter
    from tests.app.infrastructure.adapters.test_evaluation_environment import PYTHON_ID, inputs

    _, lease, _ = inputs()
    plain = DockerEnvironmentAdapter(allowed_images=[PYTHON_ID])
    owned = DockerEnvironmentAdapter(
        allowed_images=[PYTHON_ID], acceptance_owner=("project-1", "run-1")
    )
    assert "opencitadel.e04.acceptance.project" not in plain.labels(lease, "case")
    assert owned.labels(lease, "case")["opencitadel.e04.acceptance.project"] == "project-1"
    assert owned.labels(lease, "case")["opencitadel.e04.acceptance.run"] == "run-1"
    assert owned.labels(lease, "case")["opencitadel.e04.namespace"] == lease.namespace
    with pytest.raises(ValueError, match="acceptance_owner"):
        DockerEnvironmentAdapter(allowed_images=[PYTHON_ID], acceptance_owner=("bad scope", "run"))


@pytest.mark.asyncio
async def test_http_broker_requires_existing_auth_and_closed_protocol(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx

    from app.infrastructure.evaluation import broker_service
    from app.infrastructure.external.sandbox.broker import create_broker_app
    from core.config import DeploymentSettings
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    version, lease, operation = inputs()
    physical = AsyncMock(return_value={"resources": []})
    monkeypatch.setattr(
        broker_service, "EvaluationBroker", lambda settings: SimpleNamespace(lifecycle=physical)
    )
    app = create_broker_app(
        DeploymentSettings(
            sandbox_broker_token="b" * 32,
            sandbox_image="sandbox:test",
            sandbox_name_prefix="opencitadel-sandbox",
            evaluation_local_docker_enabled=True,
        )
    )
    payload = {
        "lease": lease.model_dump(mode="json"),
        "operation": operation.model_dump(mode="json"),
        "version": version.model_dump(mode="json"),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://broker"
    ) as client:
        assert (await client.post("/v1/evaluation/lifecycle", json=payload)).status_code == 401
        assert (
            await client.post(
                "/v1/evaluation/lifecycle", json=payload, headers={"Authorization": "Bearer wrong"}
            )
        ).status_code == 401
        headers = {"Authorization": "Bearer " + "b" * 32}
        assert (
            await client.post(
                "/v1/evaluation/lifecycle",
                json={**payload, "command": ["rm", "foreign"]},
                headers=headers,
            )
        ).status_code == 422
        physical.assert_not_awaited()
        assert (
            await client.post("/v1/evaluation/lifecycle", json=payload, headers=headers)
        ).status_code == 200
        physical.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_control_returns_broker_receipt_without_local_cli():
    import base64
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx

    from app.infrastructure.adapters.evaluation_sandbox import LeaseControlTransport
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    _, lease, _ = inputs()
    adapter = SimpleNamespace(
        control=AsyncMock(
            return_value={
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body": base64.b64encode(b'{"ok":true}').decode(),
            }
        ),
        command=AsyncMock(),
    )
    async with httpx.AsyncClient(
        transport=LeaseControlTransport(adapter, lease, "token"), base_url="http://sandbox"
    ) as client:
        result = await client.post("/api/shell/exec", json={"command": "owned"})
    assert result.json() == {"ok": True}
    adapter.command.assert_not_awaited()


def test_api_kernel_inventory_requires_broker_instead_of_local_cli(tmp_path):
    import json

    from app.infrastructure.evaluation.environment_inventory import build_environment_registry
    from core.config import DeploymentSettings

    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "images": [],
                "fixture_image": "sha256:" + "a" * 64,
                "bootstrap_image": "sha256:" + "b" * 64,
                "targets": [],
                "credentials": [],
            }
        )
    )
    settings = DeploymentSettings(
        env="test",
        evaluation_local_docker_enabled=True,
        evaluation_test_inventory_path=str(inventory),
    )
    with pytest.raises(ValueError, match="broker_required"):
        build_environment_registry(settings, broker=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/reset",
        "http://attacker/api/file/read-file",
        "/api/file/read-file?token=x",
        "/api/shell/exec-command#fragment",
    ],
)
async def test_broker_control_rejects_nonallowlisted_paths_before_lease_io(path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.infrastructure.evaluation.broker_service import EvaluationBroker

    broker = object.__new__(EvaluationBroker)
    broker.checked = AsyncMock()
    with pytest.raises(ValueError, match="control_path_forbidden"):
        await broker.control(SimpleNamespace(path=path, method="POST"))
    broker.checked.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connect", "read_after_dispatch"])
@pytest.mark.parametrize("phase", ["prepare", "cleanup"])
async def test_broker_transport_failure_is_durably_handled_by_cleanup_worker(
    monkeypatch, failure, phase
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx

    from app.application.evaluation.environment_service import EnvironmentWorker
    from app.application.evaluation.runtime import EvaluationRuntime
    from app.infrastructure.evaluation.broker_adapter import BrokerEnvironmentAdapter
    from tests.app.infrastructure.adapters.test_evaluation_environment import inputs

    version, lease, operation = inputs()
    operation = operation.model_copy(update={"phase": phase})
    lease = lease.model_copy(update={"requester": {"user_id": "owned"}})
    error = (
        httpx.ConnectError("offline")
        if failure == "connect"
        else httpx.ReadTimeout("reply lost after send")
    )
    client = SimpleNamespace(post=AsyncMock(side_effect=error))

    @asynccontextmanager
    async def http_client(**kwargs):
        yield client

    monkeypatch.setattr(httpx, "AsyncClient", http_client)
    adapter = object.__new__(BrokerEnvironmentAdapter)
    adapter.broker_url, adapter.broker_token = "http://broker", "secret"
    adapter.request_observer = None
    adapter.validate = lambda *args: None
    repository = SimpleNamespace(
        claim=AsyncMock(return_value=(operation, lease)),
        registered=AsyncMock(return_value=version),
        complete=AsyncMock(return_value=True),
    )
    committed = []

    @asynccontextmanager
    async def factory():
        async def commit():
            committed.append(True)

        yield SimpleNamespace(
            evaluation_environment=repository,
            evaluation_dataset=SimpleNamespace(authorize=AsyncMock()),
            commit=commit,
        )

    worker = EnvironmentWorker(factory, SimpleNamespace(resolve=lambda *args: adapter))
    following = AsyncMock()
    runtime = EvaluationRuntime(
        scheduler=None,
        rules=None,
        judge=None,
        reviews=None,
        discover=None,
        cleanup=[lambda: worker.process("owned", operation.id), following],
    )
    await runtime.clean()
    assert repository.complete.await_args.kwargs["error"] == "environment_unknown_operation"
    assert repository.complete.await_args.args[1] == operation
    assert repository.complete.await_args.args[2] == {}
    assert len(committed) == 2
    client.post.assert_awaited_once()
    following.assert_awaited_once()
