"""Separate API ingress from kernel authority; neither borrows the other's role."""

from app.application.execution.definition_management import DefinitionManagementService
from app.infrastructure.execution.definition_management import PostgresDefinitionManagement


def build_definition_management(*, sessions, authorization):
    return DefinitionManagementService(
        PostgresDefinitionManagement(session_factory=sessions, authorization=authorization)
    )
