from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    OperationsPolicy,
    RuntimePolicyStaleError,
    SandboxOperationsPolicy,
)
from app.infrastructure.external.sandbox.factory import SandboxFactory
from app.infrastructure.external.sandbox.settings import SandboxDeployment
from tests.runtime_policy_support import MutablePolicyReader


def _deployment(*, image: str, network: str) -> SandboxDeployment:
    return SandboxDeployment(
        driver="docker",
        address=None,
        image=image,
        name_prefix="opencitadel-sandbox",
        network=network,
        chrome_args="",
        https_proxy=None,
        http_proxy=None,
        no_proxy=None,
        k8s_namespace="default",
        k8s_pod_label="app=opencitadel-sandbox",
    )


def _coordination() -> dict[str, AsyncMock]:
    return {"quota_store": AsyncMock(), "activity_store": AsyncMock()}


@pytest.mark.asyncio
async def test_current_settings_uses_fresh_sandbox_policy_and_revision() -> None:
    reader = MutablePolicyReader()
    factory = SandboxFactory(
        deployment=_deployment(image="sandbox:first", network="net-a"),
        operations=reader,
        **_coordination(),
        clock=lambda: datetime(2026, 8, 26, tzinfo=UTC),
    )

    first = await factory.current_settings(require_fresh=True)
    reader.set_operations(OperationsPolicy(sandbox=SandboxOperationsPolicy(memory_limit="512m")))
    second = await factory.current_settings(require_fresh=True)

    assert first.policy.memory_limit == "2g"
    assert second.policy.memory_limit == "512m"
    assert second.operations_revision_id != first.operations_revision_id
    assert [fresh for fresh, _now in reader.operations_calls] == [True, True]


@pytest.mark.asyncio
async def test_factories_do_not_share_deployment_or_policy_state() -> None:
    first = SandboxFactory(
        deployment=_deployment(image="sandbox:first", network="net-a"),
        operations=MutablePolicyReader(
            operations=OperationsPolicy(sandbox=SandboxOperationsPolicy(memory_limit="512m"))
        ),
        **_coordination(),
    )
    second = SandboxFactory(
        deployment=_deployment(image="sandbox:second", network="net-b"),
        operations=MutablePolicyReader(
            operations=OperationsPolicy(sandbox=SandboxOperationsPolicy(memory_limit="1g"))
        ),
        **_coordination(),
    )

    first_settings = await first.current_settings(require_fresh=True)
    second_settings = await second.current_settings(require_fresh=True)

    assert (first_settings.deployment.image, first_settings.policy.memory_limit) == (
        "sandbox:first",
        "512m",
    )
    assert (second_settings.deployment.image, second_settings.policy.memory_limit) == (
        "sandbox:second",
        "1g",
    )


@pytest.mark.asyncio
async def test_fresh_policy_failure_blocks_sandbox_settings() -> None:
    reader = MutablePolicyReader()
    reader.error = RuntimePolicyStaleError(age_seconds=61)
    factory = SandboxFactory(
        deployment=_deployment(image="sandbox:first", network="net-a"),
        operations=reader,
        **_coordination(),
    )

    with pytest.raises(RuntimePolicyStaleError):
        await factory.current_settings(require_fresh=True)

    assert reader.operations_calls[0][0] is True


@pytest.mark.asyncio
async def test_allocation_uses_current_resource_limit_and_owner_scope() -> None:
    reader = MutablePolicyReader()
    factory = SandboxFactory(
        deployment=_deployment(image="sandbox:first", network="net-a"),
        operations=reader,
        **_coordination(),
    )
    sandbox = SimpleNamespace()
    factory.pool.acquire = AsyncMock(return_value=sandbox)
    reader.set_operations(OperationsPolicy(sandbox=SandboxOperationsPolicy(memory_limit="512m")))
    owner = OwnerScope.personal("user-1")

    created = await factory.create(owner_scope=owner)

    assert created is sandbox
    assert created.owner_scope == owner
    effective = factory.pool.acquire.await_args.args[0]
    assert effective.policy.memory_limit == "512m"
    assert reader.operations_calls[-1][0] is True
