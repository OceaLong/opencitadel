"""Public batch commands contain version references, never execution authority."""

from uuid import UUID

from pydantic import Field, StrictInt

from app.domain.evaluation.batch import BatchEnvironment, CaseResult
from app.domain.evaluation.dataset import ImmutableModel


class StartBatchRequest(ImmutableModel):
    suite_version: UUID
    preflight_revision: StrictInt = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=255)


class BatchCommandRequest(ImmutableModel):
    request_id: str = Field(min_length=1, max_length=255)


class ResultPage(ImmutableModel):
    items: tuple[CaseResult, ...]
    next_cursor: str | None = None


class BatchEnvironmentPage(ImmutableModel):
    items: tuple[BatchEnvironment, ...]
    next_cursor: str | None = None
