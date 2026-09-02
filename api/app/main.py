"""Side-effect-free FastAPI factory for the greenfield application."""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from starlette.middleware.sessions import SessionMiddleware

from app.composition.api import open_api_runtime
from app.composition.tasks import TaskFailure
from app.composition.types import ApiRuntime
from app.interfaces.endpoints.routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.middleware.api_cache_policy import ApiCachePolicyMiddleware
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from app.interfaces.middleware.csrf import CsrfMiddleware
from app.interfaces.middleware.request_logging import install_request_logging
from app.observability.otel import setup_observability
from core.config import DeploymentSettings, load_deployment_settings

logger = logging.getLogger(__name__)
ApiRuntimeFactory = Callable[..., AbstractAsyncContextManager[ApiRuntime]]


def signal_process_shutdown() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def _verify_db_migrations(settings: DeploymentSettings) -> None:
    if settings.env == "test":
        return
    config = Config("alembic.ini")
    expected = set(ScriptDirectory.from_config(config).get_heads())
    engine = create_engine(settings.sqlalchemy_database_uri.replace("+asyncpg", ""))
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads() or [])
    except Exception:
        if settings.env == "development":
            logger.warning("database schema verification skipped: database unavailable")
            return
        raise
    finally:
        engine.dispose()
    if current != expected:
        raise RuntimeError(
            "Greenfield database recreation required: "
            f"current_heads={sorted(current)}, expected_heads={sorted(expected)}"
        )


def _install_http(application: FastAPI, settings: DeploymentSettings) -> None:
    origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    wildcard = "*" in origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[] if wildcard else origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-CSRF-Token", "X-Workspace-Id"],
    )
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    register_exception_handlers(application)
    application.add_middleware(AuthContextMiddleware)
    application.add_middleware(CsrfMiddleware)
    application.add_middleware(ApiCachePolicyMiddleware)
    install_request_logging(application)
    application.include_router(router, prefix="/api")


def create_app(
    settings: DeploymentSettings | None = None,
    *,
    runtime_factory: ApiRuntimeFactory = open_api_runtime,
    process_shutdown: Callable[[], None] = signal_process_shutdown,
) -> FastAPI:
    resolved = settings or load_deployment_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        _verify_db_migrations(resolved)
        setup_observability(application, settings=resolved)

        def on_critical_failure(_failure: TaskFailure) -> None:
            process_shutdown()

        async with runtime_factory(
            resolved,
            on_critical_failure=on_critical_failure,
        ) as runtime:
            application.state.runtime = runtime
            try:
                yield
            finally:
                runtime.readiness.mark_not_ready()
                application.state.runtime = None

    application = FastAPI(
        title="OpenCitadel",
        description="Governed private Agent runtime with a PostgreSQL event journal.",
        version="2.0.0",
        lifespan=lifespan,
    )
    application.state.runtime = None
    _install_http(application, resolved)
    return application


__all__ = ["create_app"]
