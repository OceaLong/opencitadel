"""Deployment-owned Run execution occupancy, separate from provider sends."""

from typing import Annotated

from pydantic import Field, StrictInt

from app.domain.evaluation.dataset import ImmutableModel

Positive = Annotated[StrictInt, Field(gt=0)]


class ExecutionSlotPolicy(ImmutableModel):
    revision: Positive
    subject_limit: Positive = 5
    judge_limit: Positive = 2
    global_limit: Positive | None = None
    user_limit: Positive | None = None


class ExecutionCapacityUnavailable(Exception):
    """A command must remain unaccepted until execution capacity is available."""
