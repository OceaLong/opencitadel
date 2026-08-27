"""Side-effect-free FastAPI application factory."""

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

from app.application.ports.crypto import BootstrapAdminCredentials
from app.application.security.authorization_context import authorization_scope
from app.application.services.bootstrap_service import bootstrap_data
from app.composition.api import open_api_runtime
from app.composition.tasks import TaskFailure
from app.composition.types import ApiRuntime
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.observability.otel import setup_observability
from app.interfaces.endpoints.a2a_routes import a2a_router, well_known_router
from app.interfaces.endpoints.routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from app.interfaces.middleware.csrf import CsrfMiddleware
from app.interfaces.middleware.rate_limit import maybe_install_rate_limit
from app.interfaces.middleware.request_logging import install_request_logging
from core.config import DeploymentSettings, load_deployment_settings

logger = logging.getLogger(__name__)

ApiRuntimeFactory = Callable[..., AbstractAsyncContextManager[ApiRuntime]]

OPENAPI_TAGS = [
    {
        "name": "状态模块",
        "description": "包含 **状态监测** 等API 接口，用于监测系统的运行状态。",
    }
]


def signal_process_shutdown() -> None:
    """Ask the ASGI server to perform its normal lifespan shutdown."""

    os.kill(os.getpid(), signal.SIGTERM)


def _verify_db_migrations(settings: DeploymentSettings) -> None:
    """Fail fast when the required database schema is not at its head."""

    if settings.env == "test":
        return
    try:
        alembic_config = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_config)
        expected_heads = set(script.get_heads())
        engine = create_engine(settings.sqlalchemy_database_uri.replace("+asyncpg", ""))
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(connection)
                current_heads = set(context.get_current_heads() or [])
        finally:
            engine.dispose()
    except (OSError, RuntimeError, ValueError) as exc:
        if settings.env == "development":
            logger.warning("Migration verification skipped (DB unavailable): %s", exc)
            return
        raise
    if not expected_heads:
        raise RuntimeError("No Alembic heads found in migration scripts")
    if current_heads != expected_heads:
        raise RuntimeError(
            f"Database migration required: current_heads={sorted(current_heads)}, "
            f"expected_heads={sorted(expected_heads)}. Run the migration entrypoint first."
        )


async def _shutdown_agent_service(runtime: ApiRuntime) -> None:
    try:
        await runtime.agent_service.shutdown()
    except TimeoutError:
        logger.warning("AgentService shutdown timed out")
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("AgentService shutdown failed: %s", exc)


def _install_application(
    application: FastAPI,
    settings: DeploymentSettings,
) -> None:
    origins = [value.strip() for value in settings.cors_origins.split(",") if value.strip()]
    allow_all = "*" in origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[] if allow_all else origins,
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
    install_request_logging(application)
    application.add_middleware(AuthContextMiddleware)
    application.add_middleware(CsrfMiddleware)
    maybe_install_rate_limit(
        application,
        fail_closed=settings.env.lower() == "production",
        trusted_proxy_cidrs=tuple(
            value.strip() for value in settings.trusted_proxy_cidrs.split(",") if value.strip()
        ),
    )
    application.include_router(well_known_router)
    application.include_router(a2a_router, prefix="/api/a2a")
    application.include_router(router, prefix="/api")


def create_app(
    settings: DeploymentSettings | None = None,
    *,
    runtime_factory: ApiRuntimeFactory = open_api_runtime,
    process_shutdown: Callable[[], None] = signal_process_shutdown,
) -> FastAPI:
    """Build one FastAPI instance whose lifespan owns one typed runtime."""

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
                with authorization_scope(AuthorizationContext.system("api-bootstrap")):
                    await bootstrap_data(
                        uow_factory=runtime.uow_factory,
                        skill_service=runtime.skill_service,
                        credentials=BootstrapAdminCredentials(
                            email=resolved.bootstrap_admin_email,
                            password=resolved.bootstrap_admin_password,
                        ),
                        password_hasher=runtime.password_hasher,
                    )
                yield
            finally:
                runtime.readiness.mark_not_ready()
                await _shutdown_agent_service(runtime)
                application.state.runtime = None

    application = FastAPI(
        title="OpenCitadel通用智能体",
        description=(
            "OpenCitadel是一个通用的AI Agent系统，可以完全私有部署，"
            "使用A2A+MCP连接Agent/Tool，同时支持在沙箱中运行各种内置工具和操作"
        ),
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
        version="1.0.0",
    )
    application.state.runtime = None
    _install_application(application, resolved)
    return application


__all__ = ["create_app"]
