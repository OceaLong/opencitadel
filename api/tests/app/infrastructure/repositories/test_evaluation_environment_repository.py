# ruff: noqa: F811
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.evaluation_image_support import evaluation_test_image
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


def version():
    from app.domain.evaluation.environment import EnvironmentVersion, ImageIdentity

    return EnvironmentVersion(
        id=uuid4(),
        image_digest=ImageIdentity(kind="local_content_id", value="sha256:" + "a" * 64),
        fixture_revision="1",
        reset_adapter="test",
        adapter_revision="1",
        healthcheck_revision="1",
    )


async def test_registry_admin_scope_and_current_revision(datasets):
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    service = EnvironmentService(ds.uow_factory, AdapterRegistry())
    item = version()
    with pytest.raises(PermissionError):
        await service.register(scope, principal, "environment", item, request_id=str(uuid4()))
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    # Registration fails closed when deployment adapter is absent, even for admin.
    with pytest.raises(ValueError, match="adapter_unavailable"):
        await service.register(scope, principal, "environment", item, request_id=str(uuid4()))
    async with ds.uow_factory(AuthorizationContext.for_principal(principal, scope=scope)) as uow:
        await uow.evaluation_environment.register(scope, "environment", item)
        await uow.commit()
    async with ds.uow_factory(AuthorizationContext.for_principal(principal, scope=scope)) as uow:
        assert await uow.evaluation_environment.registered(scope, "environment", item.id) == item


async def test_generation_claim_and_dirty_fence_survive_expiry(datasets, isolated_database):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    ds, scope, principal, _, _ = datasets
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=isolated_database[0].url.database)
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def kernel():
        return DBUnitOfWork(
            sessions,
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=AuthorizationContext.system("environment-test"),
        )

    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=uuid4(),
        case_slot=CaseSlot(
            workspace="user:" + scope.user_id,
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
        expires_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    try:
        async with kernel() as uow:
            repo = uow.evaluation_environment
            await repo.allocate(scope, lease, (), concurrency=2)
            lease = await repo.begin(scope, lease, "preparing", "prepare")
            pending = await repo.pending()
            operation, _ = await repo.claim(scope, pending[0]["id"])
            await uow.commit()
        async with kernel() as uow:
            repo = uow.evaluation_environment
            assert await repo.complete(scope, operation, {}, error="prepare_failed")
            assert (await repo.lease(scope, lease.id)).state == "quarantine"
            assert not await repo.complete(scope, operation, {})
            await uow.commit()
        async with ds.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as uow:
            with pytest.raises(PermissionError, match="environment_allocation_kernel_required"):
                await uow.evaluation_environment.allocate(
                    scope, lease.model_copy(update={"id": uuid4()}), (), concurrency=2
                )
    finally:
        await engine.dispose()


async def test_environment_registry_http_audit_and_foreign_admin_scope(datasets):
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import TestTarget
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.user import GlobalRole
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    target = TestTarget(
        id=uuid4(),
        physical_resource="owned-fixture:" + str(uuid4()),
        kind="http",
        endpoint="http://allowed.e04.test:8081",
    )
    service = EnvironmentService(ds.uow_factory, AdapterRegistry(targets=(target,)))
    request = str(uuid4())
    first = await service.register(scope, principal, "target", target, request_id=request)
    assert await service.register(scope, principal, "target", target, request_id=request) == first
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM audit_logs WHERE action='evaluation.environment.register' AND resource_id=:id"
                ),
                {"id": str(target.id)},
            )
            == 1
        )
    with pytest.raises((PermissionError, ValueError)):
        await service.register(
            OwnerScope.personal("foreign"), principal, "target", target, request_id=str(uuid4())
        )


@pytest.fixture
async def environment_kernel(datasets, isolated_database):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=isolated_database[0].url.database)
    )

    def factory(auth=None):
        return DBUnitOfWork(
            async_sessionmaker(engine, expire_on_commit=False),
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=auth or AuthorizationContext.system("environment-test"),
        )

    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    __import__("os").environ.get("E04_REAL_DOCKER") != "1",
    reason="explicit owned Docker mutation opt-in",
)
@pytest.mark.parametrize("networked", [False, True])
async def test_actual_db_worker_owned_container_lifecycle(datasets, environment_kernel, networked):
    from app.application.evaluation.environment_service import EnvironmentService, EnvironmentWorker
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import (
        CaseSlot,
        EnvironmentVersion,
        ImageIdentity,
    )
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from app.infrastructure.adapters.evaluation_environment import DockerEnvironmentAdapter
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    image = evaluation_test_image(
        "FIXTURE", "sha256:4185d2c55ba89731509d80ef7972b16c75553fdda1cb61d46ede5aac3595b1aa"
    )
    targets = ()
    if networked:
        from app.domain.evaluation.environment import TestTarget, VersionRef
        from app.infrastructure.adapters.evaluation_environment import (
            DockerNetworkEnvironmentAdapter,
        )

        fixture = image
        image = evaluation_test_image(
            "SANDBOX", "sha256:2985396f077503042754ff2673e0d10a3c8903c03b9a38ec711a9e64947659fd"
        )
        adapter = DockerNetworkEnvironmentAdapter(
            allowed_images=(image,),
            fixture_image=fixture,
            bootstrap_image=evaluation_test_image(
                "BOOTSTRAP",
                "sha256:b200139c59c438b5ab199e7b0886c95583cfb1a2508de05dd77a25930d99d13f",
            ),
            local_content_ids=True,
        )
        target = TestTarget(
            id=uuid4(),
            kind="http",
            physical_resource="owned-fixture",
            endpoint="http://allowed.e04.test:8081",
        )
        targets = (target,)
    else:
        adapter = DockerEnvironmentAdapter(allowed_images=(image,), local_content_ids=True)
    registry = AdapterRegistry(adapters={adapter.revision: adapter}, targets=targets)
    service = EnvironmentService(ds.uow_factory, registry)
    worker = EnvironmentWorker(environment_kernel, registry)
    value = EnvironmentVersion(
        id=uuid4(),
        image_digest=ImageIdentity(kind="local_content_id", value=image),
        fixture_revision="empty-home-v1",
        reset_adapter=adapter.revision,
        adapter_revision=adapter.revision,
        healthcheck_revision="owned-absence-v1",
        allowed_targets=tuple(VersionRef(id=t.id, revision=t.revision) for t in targets)
        if networked
        else (),
    )
    for target in targets:
        await service.register(scope, principal, "target", target, request_id=str(uuid4()))
    await service.register(scope, principal, "environment", value, request_id=str(uuid4()))
    leases = []
    exact = []

    async def drain():
        for _ in range(6):
            async with environment_kernel() as uow:
                rows = await uow.evaluation_environment.pending()
            if not rows:
                return
            for row in rows:
                assert await worker.process(scope, row["id"])
        raise AssertionError("environment lifecycle failed to converge")

    try:
        for repeat in (1, 2):
            slot = CaseSlot(
                workspace="user:" + scope.user_id,
                batch_id=uuid4(),
                case_id=uuid4(),
                config_version=uuid4(),
                repeat=repeat,
            )
            async with environment_kernel() as uow:
                lease = await service.allocate_in_uow(uow, scope, principal, value.id, slot)
                leases.append(lease)
                await uow.commit()
            await drain()
            async with environment_kernel() as uow:
                ready = await uow.evaluation_environment.lease(scope, lease.id)
                assert ready.state == "ready"
                assert ready.actual_versions["actual_image_id"] == image
                exact.extend(ready.resources)
            case = await adapter.case(ready)
            await adapter.command(
                "exec",
                case,
                "sh",
                "-c",
                "test ! -e /home/ubuntu/contamination && echo case-only > /home/ubuntu/contamination",
            )
            async with environment_kernel() as uow:
                await service.cleanup_in_uow(uow, scope, ready.id)
                await uow.commit()
            await drain()
            async with environment_kernel() as uow:
                clean = await uow.evaluation_environment.lease(scope, ready.id)
                assert clean.state == "verified_clean"
                assert clean.resources == ()
                assert clean.actual_versions["actual_image_id"] == image
    finally:
        for lease in leases:
            await adapter.cleanup(lease, None, value, ())
        print("E04_DURABLE_OWNED_RESOURCES=" + __import__("json").dumps(exact, sort_keys=True))


@pytest.mark.parametrize("uncertainty", ["cancel", "takeover", "timeout"])
async def test_shared_alias_fence_unknown_prepare_and_admin_verified_repair(
    datasets, environment_kernel, uncertainty
):
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease, TestTarget
    from app.domain.evaluation.errors import DatasetConflict
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from tests.app.execution_test_support import execution_admin_session

    _ds, scope, principal, _, _ = datasets
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    target = TestTarget(
        id=uuid4(),
        physical_resource="shared:" + str(uuid4()),
        kind="http",
        endpoint="http://controlled-shared.e04.test:8081",
        shared=True,
        reset_adapter="controlled-shared-v1",
    )
    alias = target.model_copy(update={"id": uuid4()})
    service = EnvironmentService(environment_kernel, AdapterRegistry())

    def new_lease():
        return EnvironmentLease(
            id=uuid4(),
            environment_version=uuid4(),
            case_slot=CaseSlot(
                workspace="user:" + scope.user_id,
                batch_id=uuid4(),
                case_id=uuid4(),
                config_version=uuid4(),
                repeat=1,
            ),
            generation=1,
            revision=1,
            state="allocated",
            requester=principal.model_dump(mode="json"),
            expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )

    first, second = new_lease(), new_lease()
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        await repo.register(scope, "target", target)
        await repo.register(scope, "target", alias)
        await repo.allocate(scope, first, (target,), concurrency=2)
        first = await repo.begin(scope, first, "preparing", "prepare")
        rows = await repo.pending()
        operation, _ = await repo.claim(scope, rows[0]["id"])
        await uow.commit()
    async with environment_kernel() as uow:
        with pytest.raises(DatasetConflict, match="physical_resource_busy"):
            await uow.evaluation_environment.allocate(scope, second, (alias,), concurrency=2)
    if uncertainty != "cancel":
        async with environment_kernel() as uow:
            repo = uow.evaluation_environment
            if uncertainty == "takeover":
                assert (
                    await repo.claim(
                        scope, operation.id, now=datetime.now(UTC) + timedelta(minutes=6)
                    )
                    is None
                )
            else:
                assert await repo.complete(
                    scope, operation, {}, error="environment_unknown_operation"
                )
            assert (await repo.lease(scope, first.id)).state == "quarantine"
            assert await repo.unresolved_operations(scope, first.id)
            await uow.commit()
    async with environment_kernel() as uow:
        quarantined = await service.cleanup_in_uow(uow, scope, first.id)
        assert quarantined.state == "quarantine"
        assert await uow.evaluation_environment.unresolved_operations(scope, first.id)
        await uow.commit()
    async with environment_kernel() as uow:
        with pytest.raises(ValueError, match="unresolved_operation"):
            await service.cleanup_in_uow(uow, scope, first.id, repair=True, principal=principal)
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        # Exact former worker acknowledges eventual creation, but cannot clear quarantine.
        assert not await repo.complete(
            scope, operation, {"resources": [{"id": "owned-late-resource"}]}
        )
        assert (await repo.lease(scope, first.id)).state == "quarantine"
        assert not await repo.unresolved_operations(scope, first.id)
        repaired = await service.cleanup_in_uow(
            uow, scope, first.id, repair=True, principal=principal
        )
        assert repaired.state == "cleaning"
        await uow.commit()
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        rows = await repo.pending()
        claimed = [await repo.claim(scope, row["id"]) for row in rows]
        current = [item[0] for item in claimed if item is not None]
        assert len(current) == 1
        # Claims are now durable; cleanup and verify are completed in separate transactions.
        await uow.commit()
    # Late pre-repair cleanup callback must not affect this repair revision.
    async with environment_kernel() as uow:
        assert not await uow.evaluation_environment.complete(scope, operation, {})
        assert (await uow.evaluation_environment.lease(scope, first.id)).state == "cleaning"
        with pytest.raises(DatasetConflict, match="physical_resource_busy"):
            await uow.evaluation_environment.allocate(scope, second, (alias,), concurrency=2)

    async with environment_kernel() as uow:
        assert await uow.evaluation_environment.complete(scope, current[0], {})
        await uow.commit()
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        rows = await repo.pending()
        verification, _ = await repo.claim(scope, rows[0]["id"])
        await uow.commit()
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        assert await repo.complete(scope, verification, {"verified": True, "resources": []})
        assert (await repo.lease(scope, first.id)).state == "verified_clean"
        await repo.allocate(scope, second, (alias,), concurrency=2)
        await uow.commit()


@pytest.mark.parametrize("pack", ["mcp", "a2a"])
async def test_actual_e02_environment_contract_authority_uses_caller_metadata_only(
    datasets, monkeypatch, tmp_path, pack
):
    from types import SimpleNamespace

    from app.application.evaluation.environment_authority import (
        EnvironmentPreflightAuthority,
        EvaluationExternalContracts,
    )
    from app.application.evaluation.environment_service import EnvironmentService
    from app.domain.evaluation.configuration import ConfigSelection, ExternalContractReference
    from app.domain.evaluation.environment import TestTarget, VersionRef
    from app.domain.evaluation.recording import RecordedContract
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from app.domain.services.tools.capability_policy import READ_SAFE
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, objects, _ = datasets
    connector = "e04-metadata-connector"
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.execute(
            text(
                "INSERT INTO mcp_servers(id,name,url,owner_user_id,visibility) VALUES(:id,'e04','http://allowed.e04.test:8081',:owner,'private')"
                if pack == "mcp"
                else "INSERT INTO a2a_servers(id,base_url,owner_user_id,visibility) VALUES(:id,'http://allowed.e04.test:8081',:owner,'private')"
            ),
            {"id": connector, "owner": scope.user_id},
        )
        await db.commit()
    principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    from app.domain.models.tool_policy import ToolCapability

    READ_SAFE = READ_SAFE.model_copy(update={"capability": ToolCapability.INTEGRATION_READ})
    async with execution_admin_session() as db:
        await db.execute(
            text(f"UPDATE {pack}_servers SET tool_policies=CAST(:policy AS jsonb) WHERE id=:id"),
            {
                "id": connector,
                "policy": __import__("json").dumps(
                    {
                        ("echo" if pack == "mcp" else "a2a_owned_echo"): READ_SAFE.model_dump(
                            mode="json"
                        )
                    }
                ),
            },
        )
        await db.commit()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    async with ds.uow_factory(auth) as uow:
        revision = await uow.evaluation_recording.connector_binding(scope, pack, connector)
    contract = RecordedContract(
        name=f"{pack}_owned_echo",
        pack=pack,
        schema_body={
            "type": "function",
            "function": {"name": f"{pack}_owned_echo", "parameters": {"type": "object"}},
        },
        policy=READ_SAFE,
        connector_id=connector,
        source_name="echo",
        binding_revision=revision,
        authority_revision="owned-fixture-v1",
    )
    target = TestTarget(
        id=uuid4(),
        kind=pack,
        protocol="mcp-stateless-json-2025-03-26" if pack == "mcp" else "a2a-jsonrpc-0.3",
        allowed_agent_ids=("owned-agent",) if pack == "a2a" else (),
        endpoint="http://allowed.e04.test:8081",
        physical_resource="owned-fixture",
        connector_id=connector,
        connector_revision=revision,
        contracts=(contract,),
    )

    import json
    from pathlib import Path

    from app.composition.evaluation import build_environment_registry
    from app.domain.evaluation.environment import EnvironmentVersion, ImageIdentity
    from core.config import load_deployment_settings

    inventory = json.loads(Path("../deploy/evaluation/local-fixture.example.json").read_text())  # noqa: ASYNC240 - local test fixture
    inventory["targets"] = [target.model_dump(mode="json")]
    inventory_path = tmp_path / "documented-inventory.json"
    inventory_path.write_text(json.dumps(inventory))
    registry = build_environment_registry(
        load_deployment_settings().model_copy(
            update={
                "env": "development",
                "evaluation_local_docker_enabled": True,
                "evaluation_test_inventory_path": str(inventory_path),
                "sandbox_broker_url": "http://127.0.0.1:9999",
                "sandbox_broker_token": "e04-metadata-only-broker-test-key-0001",
            }
        )
    )
    service = EnvironmentService(ds.uow_factory, registry)
    value = EnvironmentVersion(
        id=uuid4(),
        image_digest=ImageIdentity(kind="local_content_id", value=inventory["images"][0]),
        fixture_revision="empty-home-v1",
        reset_adapter="docker-http-cell-v1",
        adapter_revision="docker-http-cell-v1",
        healthcheck_revision="owned-absence-v1",
        allowed_targets=(VersionRef(id=target.id, revision=1),),
    )
    await service.register(scope, principal, "target", target, request_id=str(uuid4()))
    await service.register(scope, principal, "environment", value, request_id=str(uuid4()))

    def forbidden(*args, **kwargs):
        raise AssertionError("metadata performed discovery/decryption/object IO")

    monkeypatch.setattr(objects, "get_bytes", forbidden)
    from app.domain.services.tools.mcp import MCPTool
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher

    monkeypatch.setattr(ApiKeyCipher, "decrypt_versioned", forbidden)
    monkeypatch.setattr(MCPTool, "initialize", forbidden)
    reference = ExternalContractReference(kind="environment", version_id=value.id)
    config = SimpleNamespace(
        selection=ConfigSelection(
            model_id="test", tool_names=(contract.name,), external_contract_ref=reference
        ),
        snapshot={
            "contracts": [
                {
                    "name": contract.name,
                    "pack": contract.pack,
                    "schema": contract.schema_body,
                    "policy": contract.policy.model_dump(mode="json"),
                    "connector_id": contract.connector_id,
                    "binding_revision": contract.binding_revision,
                    "authority_revision": contract.authority_revision,
                }
            ]
        },
    )
    async with ds.uow_factory(auth) as uow:
        result = await EvaluationExternalContracts(uow, registry).contracts(
            scope, principal, (contract.name,), reference=reference
        )
        assert result[0].schema_body == contract.schema_body
        evidence = await EnvironmentPreflightAuthority(registry).check_in_uow(
            scope,
            SimpleNamespace(environment_version=value.id),
            [config],
            for_start=True,
            uow=uow,
            principal=principal,
        )
        assert evidence.ready
    async with execution_admin_session() as db:
        await db.execute(
            text(
                f"UPDATE {pack}_servers SET {'url' if pack == 'mcp' else 'base_url'}='http://changed.invalid' WHERE id=:id"
            ),
            {"id": connector},
        )
        await db.commit()
    async with ds.uow_factory(auth) as uow:
        with pytest.raises(ValueError, match="test_connector_changed"):
            await EvaluationExternalContracts(uow, registry).contracts(
                scope, principal, (contract.name,), reference=reference
            )


async def test_actual_pre_admission_binding_current_approval_and_revocation(
    configurations, environment_kernel
):
    from types import SimpleNamespace

    from app.application.evaluation.environment_admission import prepare_environment_binding
    from app.application.evaluation.environment_runtime import EnvironmentRuntime
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease, transition
    from app.domain.execution.family import RunFamily
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
    from tests.app.execution_test_support import execution_admin_session

    suites, _ds, scope, principal = configurations
    skill_id = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO skills(id,slug,owner_user_id,visibility,allowed_tools) VALUES(:id,:id,:owner,'private',CAST(:allowed AS jsonb))"
            ),
            {"id": skill_id, "owner": principal.user_id, "allowed": '["write_file"]'},
        )
        await db.commit()
    draft = await suites.create(
        scope,
        principal,
        kind="config",
        name="Isolated",
        definition=ConfigSelection(
            model_id="e02-model", skill_id=skill_id, tool_names=("write_file",)
        ).model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    config = await suites.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    pair = await suites.policies.load_active_pair()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.AGENT)

    class Adapter:
        revision = "1"
        fixture_revisions = frozenset({"1"})
        healthcheck_revisions = frozenset({"1"})
        tool_names = frozenset({"write_file"})

        def validate(self, value, targets):
            assert targets == ()

        async def tool_pack(self, *args):
            raise AssertionError("approval must precede tool initialization")

    registry = AdapterRegistry(adapters={"test": Adapter()})
    value = version()
    slot = CaseSlot(
        workspace="user:" + scope.user_id,
        batch_id=uuid4(),
        case_id=uuid4(),
        config_version=config.id,
        repeat=1,
    )
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=value.id,
        case_slot=slot,
        generation=1,
        revision=1,
        state="allocated",
        requester=principal.model_dump(mode="json"),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    run_id = uuid4()
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        await repo.register(scope, "environment", value)
        await repo.allocate(scope, lease, (), concurrency=2)
        preparing = transition(lease, "preparing")
        await repo.save(scope, lease, preparing)
        ready = transition(preparing, "ready")
        await repo.save(scope, preparing, ready)
        await uow.commit()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    async with environment_kernel(auth) as uow:
        bound = await prepare_environment_binding(
            uow,
            suites,
            scope,
            principal,
            run_id=run_id,
            source_entity_id="isolated-case-one",
            config_version_id=config.id,
            lease_id=lease.id,
            policy_pair=pair,
            policy_snapshot=snapshot,
            registry=registry,
        )
        assert bound["source_entity_type"] == "evaluation_isolated_case"
        # No admitted run required: binding is durable before future E06 CreateRun enqueue.
        assert not await uow.db_session.scalar(
            text(
                "SELECT 1 FROM execution_stream_owners WHERE stream_type='run' AND stream_id=:run"
            ),
            {"run": str(run_id)},
        )
        await uow.commit()
    runtime = EnvironmentRuntime(environment_kernel, registry, None)
    run = SimpleNamespace(
        run_id=run_id,
        owner_scope=scope,
        source_entity_type=bound["source_entity_type"],
        source_entity_id=bound["source_entity_id"],
        policy_snapshot=snapshot,
    )
    context = SimpleNamespace(run=run, activity_id=uuid4())
    assert await runtime.active(context)
    definitions = await runtime.definitions(context)
    assert [d.name for d in definitions.definitions] == ["write_file"]
    assert definitions.definitions[0].requires_approval
    with pytest.raises(PermissionError, match="current_approval_required"):
        await runtime.invoke(
            context,
            name="write_file",
            arguments={"filepath": "/home/ubuntu/test", "content": "test"},
        )
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE skills SET allowed_tools='[]'::jsonb WHERE id=:id"), {"id": skill_id}
        )
        await db.commit()
    with pytest.raises(PermissionError, match="environment_current_tool_policy_denied"):
        await runtime.definitions(context)
    with pytest.raises(PermissionError, match="environment_current_tool_policy_denied"):
        await runtime.invoke(context, name="write_file", arguments={})
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    with pytest.raises(PermissionError, match="revoked"):
        await runtime.definitions(context)


async def test_claim_and_complete_use_lease_then_operation_lock_order(datasets, environment_kernel):
    import asyncio

    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease

    _, scope, principal, _, _ = datasets
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=uuid4(),
        case_slot=CaseSlot(
            workspace="user:" + scope.user_id,
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
        requester=principal.model_dump(mode="json"),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    async with environment_kernel() as uow:
        repo = uow.evaluation_environment
        await repo.allocate(scope, lease, (), concurrency=2)
        lease = await repo.begin(scope, lease, "preparing", "prepare")
        operation, _ = await repo.claim(scope, (await repo.pending())[0]["id"])
        await uow.commit()
    entered, start = asyncio.Event(), asyncio.Event()

    async def concurrent_claim():
        await start.wait()
        async with environment_kernel() as claiming:
            original = claiming.evaluation_environment.lease

            async def observed(*args, **kwargs):
                entered.set()
                return await original(*args, **kwargs)

            claiming.evaluation_environment.lease = observed
            result = await claiming.evaluation_environment.claim(
                scope, operation.id, now=datetime.now(UTC) + timedelta(minutes=6)
            )
            await claiming.commit()
            return result

    task = asyncio.create_task(concurrent_claim())
    try:
        async with environment_kernel() as completing:
            await completing.evaluation_environment.lease(scope, lease.id, lock=True)
            start.set()
            await asyncio.wait_for(entered.wait(), 2)
            # Claim must not hold an operation lock while waiting on this lease.
            assert await asyncio.wait_for(
                completing.evaluation_environment.complete(scope, operation, {}), 2
            )
            await completing.commit()
        assert await asyncio.wait_for(task, 2) is None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.skipif(
    __import__("os").environ.get("E04_REAL_DOCKER") != "1", reason="owned Docker opt-in"
)
@pytest.mark.parametrize("uncertainty", ["takeover", "timeout"])
async def test_delayed_real_prepare_never_releases_after_absence(
    datasets, environment_kernel, uncertainty
):
    import asyncio
    import json

    from app.application.evaluation.environment_service import EnvironmentService, EnvironmentWorker
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import CaseSlot, EnvironmentVersion, ImageIdentity
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from app.infrastructure.adapters.evaluation_environment import DockerEnvironmentAdapter, docker
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    image = evaluation_test_image(
        "FIXTURE", "sha256:4185d2c55ba89731509d80ef7972b16c75553fdda1cb61d46ede5aac3595b1aa"
    )
    entered, release = asyncio.Event(), asyncio.Event()
    physical_tasks = []

    async def delayed_command(*args, **kwargs):
        if args[0] == "create":

            async def physical_creation():
                entered.set()
                await release.wait()
                return await docker(*args, **kwargs)

            task = asyncio.create_task(physical_creation())
            physical_tasks.append(task)
            if uncertainty == "timeout":
                await entered.wait()
                # CLI timeout does not cancel the already submitted daemon mutation.
                raise TimeoutError("ambiguous daemon create")
            return await task
        return await docker(*args, **kwargs)

    adapter = DockerEnvironmentAdapter(
        allowed_images=(image,), local_content_ids=True, command=delayed_command
    )
    registry = AdapterRegistry(adapters={adapter.revision: adapter})
    service = EnvironmentService(ds.uow_factory, registry)
    worker = EnvironmentWorker(environment_kernel, registry)
    value = EnvironmentVersion(
        id=uuid4(),
        image_digest=ImageIdentity(kind="local_content_id", value=image),
        fixture_revision="empty-home-v1",
        reset_adapter=adapter.revision,
        adapter_revision=adapter.revision,
        healthcheck_revision="owned-absence-v1",
    )
    await service.register(scope, principal, "environment", value, request_id=str(uuid4()))
    async with environment_kernel() as uow:
        lease = await service.allocate_in_uow(
            uow,
            scope,
            principal,
            value.id,
            CaseSlot(
                workspace="user:" + scope.user_id,
                batch_id=uuid4(),
                case_id=uuid4(),
                config_version=uuid4(),
                repeat=1,
            ),
        )
        operation_id = (await uow.evaluation_environment.pending())[0]["id"]
        await uow.commit()
    work = asyncio.create_task(worker.process(scope, operation_id))
    exact = []
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if uncertainty == "timeout":
            assert await asyncio.wait_for(work, 5)
        async with environment_kernel() as uow:
            repo = uow.evaluation_environment
            if uncertainty == "takeover":
                assert (
                    await repo.claim(
                        scope, operation_id, now=datetime.now(UTC) + timedelta(minutes=6)
                    )
                    is None
                )
            assert await repo.unresolved_operations(scope, lease.id)
            quarantined = await service.cleanup_in_uow(uow, scope, lease.id)
            assert quarantined.state == "quarantine"
            await uow.commit()
        # Cleanup and positive absence verification occur before the delayed create.
        for _ in range(2):
            async with environment_kernel() as uow:
                rows = await uow.evaluation_environment.pending()
            for row in rows:
                await worker.process(scope, row["id"])
        async with environment_kernel() as uow:
            assert (await uow.evaluation_environment.lease(scope, lease.id)).state == "quarantine"
            with pytest.raises(ValueError, match="unresolved_operation"):
                await service.cleanup_in_uow(uow, scope, lease.id, repair=True, principal=principal)
        assert not await adapter.resources(lease)
        release.set()
        await asyncio.gather(*physical_tasks)
        if uncertainty == "takeover":
            assert not await asyncio.wait_for(work, 10)
        exact = await adapter.resources(lease)
        assert exact  # A resource really appeared after prior absence verification.
        async with environment_kernel() as uow:
            repo = uow.evaluation_environment
            assert (await repo.lease(scope, lease.id)).state == "quarantine"
            if uncertainty == "timeout":
                assert await repo.unresolved_operations(scope, lease.id)
                # The timeout has no conclusive callback: even known-owned cleanup
                # does not turn the original unknown attempt into a clean lease.
                with pytest.raises(ValueError, match="unresolved_operation"):
                    await service.cleanup_in_uow(
                        uow, scope, lease.id, repair=True, principal=principal
                    )
            else:
                assert not await repo.unresolved_operations(scope, lease.id)
                await service.cleanup_in_uow(uow, scope, lease.id, repair=True, principal=principal)
            await uow.commit()
        if uncertainty == "takeover":
            for _ in range(2):
                async with environment_kernel() as uow:
                    rows = await uow.evaluation_environment.pending()
                for row in rows:
                    await worker.process(scope, row["id"])
            async with environment_kernel() as uow:
                assert (
                    await uow.evaluation_environment.lease(scope, lease.id)
                ).state == "verified_clean"
    finally:
        release.set()
        await asyncio.gather(work, *physical_tasks, return_exceptions=True)
        await adapter.cleanup(lease, None, value, ())
        assert not await adapter.resources(lease)
        print("E04_FIX1_LATE_OWNED_RESOURCES=" + json.dumps(exact, sort_keys=True))


async def test_e10_inventory_is_admin_only_and_registration_selects_trusted_value(datasets):
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import TestTarget
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    target = TestTarget(
        id=uuid4(),
        physical_resource="e10-owned-fixture",
        kind="http",
        endpoint="http://fixture.e10.test",
    )
    service = EnvironmentService(ds.uow_factory, AdapterRegistry(targets=(target,)))
    with pytest.raises(PermissionError):
        await service.inventory(scope, principal)
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    admin = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    inventory = await service.inventory(scope, admin)
    assert inventory.targets[0].id == target.id
    assert "endpoint" not in inventory.model_dump_json()
    assert "locator" not in inventory.model_dump_json()
    import httpx
    from fastapi import FastAPI

    from app.domain.models.scope import OwnerScope, WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_environment_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_environment_service

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_environment_service] = lambda: service
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=admin
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/evaluation/environments/inventory")
        assert response.status_code == 200, response.text
        assert "endpoint" not in response.text
        assert "locator" not in response.text
        assert (await client.get("/evaluation/environments")).status_code == 200
        from tests.app.application.services.test_artifact_provenance_postgres import seed

        other, _ = await seed()
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=OwnerScope.personal(other), principal=Principal(user_id=other)
        )
        assert (await client.get("/evaluation/environments/inventory")).status_code == 403
        hidden = await client.get("/evaluation/environments")
        assert hidden.status_code == 200
        assert hidden.json()["data"]["items"] == []
    selected = await service.register_inventory(
        scope, admin, "target", target.id, target.revision, request_id=str(uuid4())
    )
    assert selected["id"] == str(target.id)
    repeated = await service.register_inventory(
        scope, admin, "target", target.id, target.revision, request_id=str(uuid4())
    )
    assert repeated == selected
    with pytest.raises(ValueError, match="test_inventory_binding_unavailable"):
        await service.register_inventory(
            scope, admin, "target", target.id, 999, request_id=str(uuid4())
        )
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='user' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    with pytest.raises(PermissionError):
        await service.inventory(scope, admin)


async def test_api_lease_status_reads_failed_revision_then_repair_without_private_metadata(
    datasets, isolated_database
):
    """Real frozen-schema SELECT grants and repository lifecycle; not executed offline."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease, transition
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    ds, scope, principal, _, _ = datasets
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=isolated_database[0].url.database)
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def kernel():
        return DBUnitOfWork(
            sessions,
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=AuthorizationContext.system("environment-test"),
        )

    batch_id = uuid4()
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=uuid4(),
        case_slot=CaseSlot(
            workspace="user:" + scope.user_id,
            batch_id=batch_id,
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    try:
        async with kernel() as work:
            repo = work.evaluation_environment
            await repo.allocate(scope, lease, (), concurrency=2)
            lease = await repo.begin(scope, lease, "preparing", "prepare")
            pending = await repo.pending()
            operation, _ = await repo.claim(scope, pending[0]["id"])
            assert await repo.complete(
                scope, operation, {"private": "receipt-secret"}, error="adapter-secret"
            )
            await work.commit()
        async with ds.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            rows = await DBEvaluationBatchRepository(work.db_session).environment_statuses(
                scope, batch_id
            )
            assert len(rows) == 1
            assert rows[0]["state"] == "quarantine"
            assert rows[0]["prior_failed_operations"] == {"prepare": 1}
            assert "secret" not in str(rows)
            assert "namespace" not in rows[0]
            assert (
                await DBEvaluationBatchRepository(work.db_session).environment_statuses(
                    scope, uuid4()
                )
                == []
            )
            assert (
                await DBEvaluationBatchRepository(work.db_session).environment_statuses(
                    OwnerScope.personal("foreign"), batch_id
                )
                == []
            )
        async with kernel() as work:
            repo = work.evaluation_environment
            current = await repo.lease(scope, lease.id)
            cleaning = transition(current, "cleaning", repair=True, administrator=True)
            await repo.save(scope, current, cleaning)
            await repo.save(scope, cleaning, transition(cleaning, "verified_clean"))
            await work.commit()
        async with ds.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            rows = await DBEvaluationBatchRepository(work.db_session).environment_statuses(
                scope, batch_id
            )
            assert rows[0]["state"] == "verified_clean"
            assert rows[0]["prior_failed_operations"] == {"prepare": 1}
    finally:
        await engine.dispose()
