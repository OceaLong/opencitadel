"""Production analysis wiring resolves a real scoped preference on each request."""

from app.application.services.execution_analysis_service import ExecutionAnalysisService
from app.infrastructure.repositories.db_analysis_preferences import DBAnalysisPreferences
from app.infrastructure.repositories.db_execution_analysis_repository import (
    DBExecutionAnalysisRepository,
)


def build_analysis_preferences(*, settings, resources):
    return DBAnalysisPreferences(
        resources.postgres.session_factory,
        signing_secret=settings.database_authorization_signing_secret,
    )


async def build_analysis_service(
    *, settings, resources, scope, principal, repository=None, preferences=None
):
    repository = repository or DBExecutionAnalysisRepository(
        resources.postgres.session_factory,
        signing_secret=settings.database_authorization_signing_secret,
    )
    preferences = preferences or build_analysis_preferences(settings=settings, resources=resources)
    preference = await preferences.get(scope, principal)
    return ExecutionAnalysisService(
        repository, principal, workspace_timezone=preference["timezone"]
    )


class AnalysisServiceFactory:
    """Runtime-owned, bounded caller cache. Every hit still checks fresh authority."""

    def __init__(self, repository, preferences, *, max_callers=32):
        from collections import OrderedDict

        self.repository = repository
        self.preferences = preferences
        self.max_callers = max_callers
        self.services = OrderedDict()

    async def __call__(self, scope, principal):
        preference = await self.preferences.get(scope, principal)
        key = (
            scope.model_dump_json(),
            principal.model_dump_json(),
            preference["timezone"],
            preference["revision"],
        )
        if key not in self.services:
            self.services[key] = ExecutionAnalysisService(
                self.repository, principal, workspace_timezone=preference["timezone"]
            )
        self.services.move_to_end(key)
        while len(self.services) > self.max_callers:
            self.services.popitem(last=False)
        return self.services[key]


def build_analysis_factory(*, settings, resources):
    return AnalysisServiceFactory(
        DBExecutionAnalysisRepository(
            resources.postgres.session_factory,
            signing_secret=settings.database_authorization_signing_secret,
        ),
        build_analysis_preferences(settings=settings, resources=resources),
    )
