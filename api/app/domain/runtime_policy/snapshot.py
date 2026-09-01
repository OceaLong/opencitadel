"""Family-bounded Execution Policy snapshots frozen into Run history."""

from __future__ import annotations

import hmac
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.execution.family import RunFamily
from app.domain.runtime_policy.canonical import policy_digest
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError
from app.domain.runtime_policy.execution import (
    ActivityExecutionPolicy,
    AgentExecutionPolicy,
    ExecutionPolicy,
    KnowledgeBaseExecutionPolicy,
    KnowledgeRerankPolicy,
    KnowledgeRetrievalPolicy,
    MemoryExecutionPolicy,
    ModelResiliencePolicy,
)
from app.domain.runtime_policy.revision import ActiveExecutionPolicy


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommonRunPolicy(_SnapshotModel):
    activity: ActivityExecutionPolicy
    model_resilience: ModelResiliencePolicy


class KnowledgeRetrievalRunPolicy(_SnapshotModel):
    vector_enabled: bool
    graph_enabled: bool
    retrieval: KnowledgeRetrievalPolicy
    rerank: KnowledgeRerankPolicy


class AgentRunPolicy(_SnapshotModel):
    kind: Literal["agent"] = "agent"
    agent: AgentExecutionPolicy
    memory: MemoryExecutionPolicy
    knowledge_retrieval: KnowledgeRetrievalRunPolicy


class AskRunPolicy(_SnapshotModel):
    kind: Literal["ask"] = "ask"
    agent: AgentExecutionPolicy
    memory: MemoryExecutionPolicy
    knowledge_retrieval: KnowledgeRetrievalRunPolicy


class KnowledgeIngestRunPolicy(_SnapshotModel):
    kind: Literal["kb_ingest"] = "kb_ingest"
    knowledge_base: KnowledgeBaseExecutionPolicy


class AutomationRunPolicy(_SnapshotModel):
    kind: Literal["automation"] = "automation"


class PatrolRunPolicy(_SnapshotModel):
    kind: Literal["patrol"] = "patrol"


class RemediationRunPolicy(_SnapshotModel):
    kind: Literal["remediation"] = "remediation"


FamilyRunPolicy = Annotated[
    AgentRunPolicy
    | AskRunPolicy
    | KnowledgeIngestRunPolicy
    | AutomationRunPolicy
    | PatrolRunPolicy
    | RemediationRunPolicy,
    Field(discriminator="kind"),
]


class RunPolicySnapshot(_SnapshotModel):
    schema_version: Literal[1] = 1
    execution_revision_id: UUID
    execution_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    family: RunFamily
    common: CommonRunPolicy
    family_policy: FamilyRunPolicy

    @model_validator(mode="after")
    def validate_family(self) -> RunPolicySnapshot:
        if self.family.value != self.family_policy.kind:
            raise ValueError("Run policy family mismatch")
        return self


def _knowledge_retrieval(policy: ExecutionPolicy) -> KnowledgeRetrievalRunPolicy:
    return KnowledgeRetrievalRunPolicy(
        vector_enabled=policy.knowledge_base.vector_enabled,
        graph_enabled=policy.knowledge_base.graphrag.enabled,
        retrieval=policy.knowledge_base.retrieval,
        rerank=policy.knowledge_base.rerank,
    )


def _family_policy(policy: ExecutionPolicy, family: RunFamily) -> FamilyRunPolicy:
    if family is RunFamily.AGENT:
        return AgentRunPolicy(
            agent=policy.agent,
            memory=policy.memory,
            knowledge_retrieval=_knowledge_retrieval(policy),
        )
    if family is RunFamily.ASK:
        return AskRunPolicy(
            agent=policy.agent,
            memory=policy.memory,
            knowledge_retrieval=_knowledge_retrieval(policy),
        )
    if family is RunFamily.KB_INGEST:
        return KnowledgeIngestRunPolicy(knowledge_base=policy.knowledge_base)
    if family is RunFamily.AUTOMATION:
        return AutomationRunPolicy()
    if family is RunFamily.PATROL:
        return PatrolRunPolicy()
    if family is RunFamily.REMEDIATION:
        return RemediationRunPolicy()
    raise ValueError(f"unsupported Run family: {family}")


def _digest_material(
    *,
    execution_revision_id: UUID,
    execution_policy_digest: str,
    family: RunFamily,
    common: CommonRunPolicy,
    family_policy: FamilyRunPolicy,
) -> dict:
    return {
        "execution_revision_id": str(execution_revision_id),
        "execution_policy_digest": execution_policy_digest,
        "family": family.value,
        "common": common.model_dump(mode="json"),
        "family_policy": family_policy.model_dump(mode="json"),
    }


def derive_run_policy_snapshot(
    active: ActiveExecutionPolicy,
    family: RunFamily,
) -> RunPolicySnapshot:
    policy = active.revision.policy
    common = CommonRunPolicy(
        activity=policy.activity,
        model_resilience=policy.model_resilience,
    )
    family_policy = _family_policy(policy, family)
    material = _digest_material(
        execution_revision_id=active.revision.id,
        execution_policy_digest=active.revision.digest,
        family=family,
        common=common,
        family_policy=family_policy,
    )
    return RunPolicySnapshot(
        execution_revision_id=active.revision.id,
        execution_policy_digest=active.revision.digest,
        snapshot_digest=policy_digest(1, material),
        family=family,
        common=common,
        family_policy=family_policy,
    )


def validate_run_policy_snapshot(snapshot: RunPolicySnapshot) -> RunPolicySnapshot:
    try:
        parsed = RunPolicySnapshot.model_validate(snapshot.model_dump(mode="json"))
    except ValidationError as exc:
        raise RuntimePolicyIntegrityError("Run policy snapshot family or shape is invalid") from exc
    expected = policy_digest(
        parsed.schema_version,
        _digest_material(
            execution_revision_id=parsed.execution_revision_id,
            execution_policy_digest=parsed.execution_policy_digest,
            family=parsed.family,
            common=parsed.common,
            family_policy=parsed.family_policy,
        ),
    )
    if not hmac.compare_digest(parsed.snapshot_digest, expected):
        raise RuntimePolicyIntegrityError("Run policy snapshot digest mismatch")
    return snapshot


__all__ = [
    "AgentRunPolicy",
    "AskRunPolicy",
    "AutomationRunPolicy",
    "CommonRunPolicy",
    "FamilyRunPolicy",
    "KnowledgeIngestRunPolicy",
    "KnowledgeRetrievalRunPolicy",
    "PatrolRunPolicy",
    "RemediationRunPolicy",
    "RunPolicySnapshot",
    "derive_run_policy_snapshot",
    "validate_run_policy_snapshot",
]
