"""Caller-owned dataset persistence and independently durable object intent ports."""

from typing import Protocol
from uuid import UUID

from app.domain.models.authorization import AuthorizationContext
from app.domain.repositories.evaluation_dataset_repository import EvaluationDatasetRepository

__all__ = ["DatasetObjectIntentWriter", "EvaluationDatasetRepository"]


class DatasetObjectIntentWriter(Protocol):
    async def register(
        self, authorization: AuthorizationContext, *, dataset_id: UUID, object_id: UUID, digest: str
    ) -> str: ...


class EvaluationReviewCommands(Protocol):
    """Typed API command boundary; kernel-only operations stay in the consumer."""

    async def append_score(
        self, scope, principal, result_id, expected_revision, request_id, score
    ): ...
    async def rescore(self, scope, principal, result_id, request, request_id): ...
    async def list_pending(
        self, scope, cursor=None, limit=50, *, principal, status="pending", rubric_id=None
    ): ...
    async def get_command(self, scope, principal, command_id): ...
