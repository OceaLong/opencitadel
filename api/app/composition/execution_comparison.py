"""Comparison API factories and the existing kernel supervisor's durable diff lane."""

from app.application.services.comparison_artifact_reader import ComparisonArtifactReader
from app.application.services.comparison_diff_worker import ComparisonDiffWorker
from app.application.services.execution_comparison_service import ExecutionComparisonService
from app.infrastructure.execution.comparison_diff_compute import IsolatedDiffCompute
from app.infrastructure.repositories.db_comparison_diff_jobs import DBComparisonDiffJobs
from app.infrastructure.repositories.db_execution_comparison_repository import (
    DBExecutionComparisonRepository,
)


async def build_comparison_service(*, settings, resources, scope, principal, shared=None):
    repository = DBExecutionComparisonRepository(
        resources.postgres.session_factory,
        signing_secret=settings.database_authorization_signing_secret,
    )
    from app.composition.execution_analysis import build_analysis_preferences

    preference = await build_analysis_preferences(settings=settings, resources=resources).get(
        scope, principal
    )
    from app.application.services.comparison_body_service import ComparisonBodyService

    return ExecutionComparisonService(
        repository,
        jobs=DBComparisonDiffJobs(repository),
        workspace_timezone=preference["timezone"],
        bodies=ComparisonBodyService(
            repository,
            ComparisonArtifactReader(repository, shared.object_storage) if shared else None,
            secret=settings.database_authorization_signing_secret,
        ),
    )


def build_comparison_diff_worker(*, settings, resources, shared):
    repository = DBExecutionComparisonRepository(
        resources.postgres.session_factory,
        signing_secret=settings.database_authorization_signing_secret,
    )
    return ComparisonDiffWorker(
        DBComparisonDiffJobs(repository),
        lambda scope, principal: ComparisonArtifactReader(repository, shared.object_storage),
        IsolatedDiffCompute(),
    )
