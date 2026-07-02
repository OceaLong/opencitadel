#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
from contextlib import asynccontextmanager

from app.runtime_role import ProcessRole, set_role

set_role(ProcessRole.API)

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.container import get_api_container, init_api_container, shutdown_api_container
from app.application.services.bootstrap_service import bootstrap_data
from app.application.services.config_provider import get_runtime_config
from app.infrastructure.logging import setup_logging
from app.infrastructure.observability.logging_context import configure_structured_logging
from app.infrastructure.observability.otel import setup_observability
from app.infrastructure.storage.postgres import get_uow
from core.config import get_settings

# Wire DI container before route modules resolve FastAPI dependencies.
get_api_container().wire(
    packages=[
        "app.interfaces",
        "app.interfaces.endpoints",
    ],
)

from app.interfaces.endpoints.a2a_routes import a2a_router, well_known_router
from app.interfaces.endpoints.routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from app.interfaces.middleware.rate_limit import maybe_install_rate_limit
from app.interfaces.middleware.request_logging import install_request_logging
from app.interfaces.service_dependencies import get_agent_service, get_skill_service
from app.infrastructure.security.csrf import CsrfMiddleware
from app.infrastructure.security.jwt_service import JwtService

# 1.加载配置信息
settings = get_settings()
runtime_config = get_runtime_config()

# 2.初始化日志系统
setup_logging()
logger = logging.getLogger()


async def _connection_pool_cleanup_loop() -> None:
    from app.infrastructure.external.tools.connection_pool import A2AConnectionPool, MCPConnectionPool

    interval = 300
    while True:
        try:
            await MCPConnectionPool.release_stale()
            await A2AConnectionPool.release_stale()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("连接池回收失败: %s", exc)
        await asyncio.sleep(interval)

# 3.定义FastAPI路由tags标签
openapi_tags = [
    {
        "name": "状态模块",
        "description": "包含 **状态监测** 等API 接口，用于监测系统的运行状态。"
    }
]


def _verify_db_migrations() -> None:
    """Ensure DB schema is at head; fail fast if migrate job was not run."""
    if settings.env == "test":
        logger.info("Skipping migration verification in test environment")
        return
    try:
        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = set(script.get_heads())
        sync_url = settings.sqlalchemy_database_uri.replace("+asyncpg", "")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_heads = set(context.get_current_heads() or [])
    except Exception as exc:
        if settings.env == "development":
            logger.warning("Migration verification skipped (DB unavailable): %s", exc)
            return
        raise
    if not heads:
        raise RuntimeError("No Alembic heads found in migration scripts")
    if current_heads != heads:
        raise RuntimeError(
            f"Database migration required: current_heads={sorted(current_heads)}, "
            f"expected_heads={sorted(heads)}. "
            "Run `./migrate.sh' or `python -m app.migrate' before starting the API."
        )
    logger.info("Database schema verified at heads %s", sorted(heads))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """创建FastAPI应用生命周期上下文管理器"""
    # 0.重新初始化日志系统(uvicorn启动时dictConfig会影响根日志处理器，需要在此重新配置)
    setup_logging()
    configure_structured_logging()

    # 1.日志打印代码已经开始执行了
    logger.info("OpenCitadel正在初始化")

    # 2.校验数据库迁移版本（迁移由独立 migrate job 执行）
    _verify_db_migrations()

    container = await init_api_container()

    skill_service = await get_skill_service()
    await bootstrap_data(
        uow_factory=get_uow,
        skill_service=skill_service,
    )
    pool_cleanup_task = asyncio.create_task(_connection_pool_cleanup_loop())
    logger.info("OpenCitadel初始化完成")

    try:
        yield
    finally:
        pool_cleanup_task.cancel()
        try:
            await pool_cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            logger.info("OpenCitadel正在关闭")
            agent_service = await get_agent_service()
            await asyncio.wait_for(agent_service.shutdown(), timeout=30.0)
            logger.info("AgentService成功关闭")
        except asyncio.TimeoutError:
            logger.warning("AgentService关闭超时, 强制关闭, 部分任务将被释放")
        except Exception as e:
            logger.error(f"AgentService关闭期间出现错误: {str(e)}")

        await shutdown_api_container(container)
        logger.info("OpenCitadel应用关闭成功")


# 4.创建OpenCitadel应用实例
app = FastAPI(
    title="OpenCitadel通用智能体",
    description="OpenCitadel是一个通用的AI Agent系统，可以完全私有部署，使用A2A+MCP连接Agent/Tool，同时支持在沙箱中运行各种内置工具和操作",
    lifespan=lifespan,
    openapi_tags=openapi_tags,
    version="1.0.0",
)

# 5.配置CORS中间件，解决跨域问题
_cors_origins = [o.strip() for o in runtime_config.server.cors_origins.split(",") if o.strip()]
_allow_all_origins = "*" in _cors_origins
_effective_cors_origins = [] if _allow_all_origins else _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_effective_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-CSRF-Token", "X-Workspace-Id"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.cookie_secure,
)

# 6.注册错误处理器
register_exception_handlers(app)

# 6.0 HTTP 请求日志与 request_id
install_request_logging(app)
app.add_middleware(
    AuthContextMiddleware,
    jwt_service=JwtService(
        secret=settings.jwt_secret,
        access_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_ttl_seconds=settings.refresh_token_ttl_seconds,
    ),
)
app.add_middleware(CsrfMiddleware)

# 6.1 公开接口限流
maybe_install_rate_limit(app)

# 7.集成路由
app.include_router(well_known_router)
_runtime = get_runtime_config()
if _runtime.feature_flags.enable_agent_features:
    app.include_router(a2a_router, prefix="/api/a2a")
app.include_router(router, prefix="/api")
setup_observability(app)
