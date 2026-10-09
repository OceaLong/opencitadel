"""API export service and supervised kernel generation/retirement lanes."""

from app.application.services.execution_export_download import ExportDownloader
from app.application.services.execution_export_worker import ExecutionExportWorker
from app.infrastructure.repositories.db_execution_export_repository import (
    DBExecutionExportRepository,
)


def export_repository(*, settings, resources):
    return DBExecutionExportRepository(
        resources.postgres.session_factory,
        signing_secret=settings.database_authorization_signing_secret,
    )


def build_export_worker(*, settings, resources, shared):
    repository = export_repository(settings=settings, resources=resources)
    worker = ExecutionExportWorker(repository, shared.object_storage)

    async def cleanup():
        return await repository.cleanup(shared.object_storage)

    return worker, cleanup


async def build_export_service(*, settings, resources, shared, scope, principal):
    from app.application.services.execution_export_service import ExecutionExportService
    from app.composition.execution_analysis import build_analysis_preferences

    preference = await build_analysis_preferences(settings=settings, resources=resources).get(
        scope, principal
    )
    repository = export_repository(settings=settings, resources=resources)
    return ExecutionExportService(
        repository,
        ExportDownloader(repository, shared.object_storage),
        workspace_timezone=preference["timezone"],
    )
