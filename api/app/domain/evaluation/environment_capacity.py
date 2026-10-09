"""Deployment-owned environment capacity, separate from execution and model pools."""

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.execution_slots import Positive


class EnvironmentCapacityPolicy(ImmutableModel):
    revision: Positive = 1
    workspace_limit: Positive = 2
    global_limit: Positive | None = None
    user_limit: Positive | None = None
