"""Assemble request-authorized F06 readers without exposing persistence to routes."""

import hashlib

from app.application.services.execution_content_service import ExecutionContentService
from app.application.services.execution_event_service import ExecutionEventService
from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView


def build_execution_content_service(
    *, settings, resources, uow_factory, artifacts, files, authorization: AuthorizationContext
) -> ExecutionContentService:
    secret = hashlib.sha256(
        (settings.public_cursor_secret or settings.api_key_secret).encode()
    ).digest()
    views = ExecutionViewService(
        PostgresExecutionView(
            session_factory=resources.postgres.session_factory, authorization=authorization
        ),
        cursor_secret=secret,
    )
    return ExecutionContentService(
        lambda: uow_factory(authorization_context=authorization),
        views,
        artifacts,
        cursor_secret=hashlib.sha256(b"execution-content:" + secret).digest(),
        files=files,
    )


def build_execution_view_service(
    *, settings, resources, authorization: AuthorizationContext
) -> ExecutionViewService:
    secret = hashlib.sha256(
        (settings.public_cursor_secret or settings.api_key_secret).encode()
    ).digest()
    return ExecutionViewService(
        PostgresExecutionView(
            session_factory=resources.postgres.session_factory, authorization=authorization
        ),
        cursor_secret=secret,
    )


def build_execution_event_service(
    *, settings, resources, authorization: AuthorizationContext
) -> ExecutionEventService:
    from app.application.execution.public_projection import PublicEventCursor
    from app.infrastructure.execution.postgres_run_public_events import PostgresRunPublicEvents

    secret = hashlib.sha256(
        (settings.public_cursor_secret or settings.api_key_secret).encode()
    ).digest()
    port = PostgresRunPublicEvents(
        session_factory=resources.postgres.session_factory,
        authorization=authorization,
        cursor=PublicEventCursor(secret=secret),
    )
    return ExecutionEventService(
        port,
        cursor_secret=hashlib.sha256(b"run-events:" + secret).digest(),
        revalidate=port.revalidate,
    )
