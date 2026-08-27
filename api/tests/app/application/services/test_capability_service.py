from unittest.mock import AsyncMock, Mock

import pytest

from app.application.services.capability_service import (
    CapabilityService,
    CapabilityStateValue,
)
from app.domain.errors import BadRequestError, ConflictError
from app.domain.models.inference import InferencePurpose
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    ExecutionPolicy,
    KnowledgeBaseExecutionPolicy,
    KnowledgeRerankPolicy,
    OperationsPolicy,
    PatrolAdmissionMode,
    PatrolOperationsPolicy,
    PatrolRemediationMode,
)
from tests.runtime_policy_support import MutablePolicyReader


def _bindings(states: dict[InferencePurpose, object]):
    bindings = Mock()

    async def resolve(purpose, *, scope):
        value = states[purpose]
        if isinstance(value, Exception):
            raise value
        return value

    bindings.resolve = AsyncMock(side_effect=resolve)
    return bindings


@pytest.mark.asyncio
async def test_capability_matrix_distinguishes_configuration_and_policy() -> None:
    resolved = Mock(id="chat-1")
    service = CapabilityService(
        _bindings(
            {
                InferencePurpose.CHAT: resolved,
                InferencePurpose.EMBEDDING: ConflictError("missing"),
                InferencePurpose.RERANK: resolved,
            }
        ),
        policy_heads=MutablePolicyReader(
            execution=ExecutionPolicy(
                knowledge_base=KnowledgeBaseExecutionPolicy(
                    rerank=KnowledgeRerankPolicy(enabled=False)
                )
            )
        ),
    )

    snapshot = await service.get_capabilities(OwnerScope.personal("user-1"))

    assert snapshot.items["chat"].state is CapabilityStateValue.AVAILABLE
    assert snapshot.items["embeddings"].state is CapabilityStateValue.NOT_CONFIGURED
    assert snapshot.items["rerank"].state is CapabilityStateValue.DISABLED
    assert snapshot.items["a2a"].state is CapabilityStateValue.AVAILABLE
    assert snapshot.items["ops_patrol"].state is CapabilityStateValue.AVAILABLE
    assert snapshot.items["ops_patrol_remediation"].state is CapabilityStateValue.DISABLED


@pytest.mark.asyncio
async def test_capability_matrix_reports_degraded_and_paused() -> None:
    service = CapabilityService(
        _bindings(
            {
                InferencePurpose.CHAT: BadRequestError("credential missing"),
                InferencePurpose.EMBEDDING: Mock(id="embedding-1"),
                InferencePurpose.RERANK: BadRequestError("breaker open"),
            }
        ),
        policy_heads=MutablePolicyReader(
            operations=OperationsPolicy(
                patrol=PatrolOperationsPolicy(
                    admission=PatrolAdmissionMode.PAUSED,
                    remediation=PatrolRemediationMode.PROPOSE_ONLY,
                )
            )
        ),
    )

    snapshot = await service.get_capabilities(OwnerScope.personal("user-1"))

    assert snapshot.items["chat"].state is CapabilityStateValue.DEGRADED
    assert snapshot.items["a2a"].state is CapabilityStateValue.DEGRADED
    assert snapshot.items["ops_patrol"].state is CapabilityStateValue.DISABLED
    assert snapshot.items["ops_patrol_remediation"].state is CapabilityStateValue.DEGRADED
    assert snapshot.items["ops_patrol_remediation"].details == {"mode": "propose_only"}


@pytest.mark.asyncio
async def test_missing_scope_is_denied_without_resolving_bindings() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock()
    snapshot = await CapabilityService(
        bindings,
        policy_heads=MutablePolicyReader(),
    ).get_capabilities(None)

    assert all(state.state is CapabilityStateValue.DENIED for state in snapshot.items.values())
    bindings.resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_details_are_secret_free() -> None:
    resolved = Mock(id="model-1", credential="super-secret", base_url="https://secret")
    service = CapabilityService(
        _bindings(dict.fromkeys(InferencePurpose, resolved)),
        policy_heads=MutablePolicyReader(),
    )

    snapshot = await service.get_capabilities(OwnerScope.personal("user-1"))
    serialized = snapshot.model_dump_json()

    assert "super-secret" not in serialized
    assert "https://secret" not in serialized


@pytest.mark.asyncio
async def test_capability_contract_uses_reason_keys_and_top_level_model_id() -> None:
    resolved = Mock(id="chat-1")
    service = CapabilityService(
        _bindings(
            {
                InferencePurpose.CHAT: resolved,
                InferencePurpose.EMBEDDING: ConflictError(
                    "missing",
                    error_key="inference.errors.bindingNotConfigured",
                ),
                InferencePurpose.RERANK: resolved,
            }
        ),
        policy_heads=MutablePolicyReader(),
    )

    snapshot = await service.get_capabilities(OwnerScope.personal("user-1"))

    assert snapshot.items["chat"].model_id == "chat-1"
    assert snapshot.items["chat"].details == {}
    assert snapshot.items["embeddings"].reason_key == "inference.errors.bindingNotConfigured"
