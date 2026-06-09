#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
from contextlib import asynccontextmanager

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.infrastructure.logging import setup_logging
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.observability.logging_context import configure_structured_logging
from app.infrastructure.observability.otel import setup_observability
from app.infrastructure.storage.cos import get_cos
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.interfaces.endpoints.a2a_routes import a2a_router, well_known_router
from app.interfaces.endpoints.routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.application.services.bootstrap_service import bootstrap_data
from app.interfaces.service_dependencies import (
    get_agent_service,
    get_llm_model_service,
    get_skill_service,
)
from app.infrastructure.storage.postgres import get_uow
from core.config import get_settings

# 1.加载配置信息
settings = get_settings()

# 2.初始化日志系统
setup_logging()
logger = logging.getLogger()


async def _sandbox_cleanup_loop() -> None:
    interval = max(60, settings.sandbox_cleanup_interval_seconds)
    while True:
        try:
            removed = await DockerSandbox.cleanup_orphaned_containers()
            if removed:
                logger.info("清理孤儿沙箱容器数量: %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("清理孤儿沙箱容器失败: %s", exc)
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
        head_revision = script.get_current_head()
        sync_url = settings.sqlalchemy_database_uri.replace("+asyncpg", "")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current = context.get_current_revision()
    except Exception as exc:
        if settings.env == "development":
            logger.warning("Migration verification skipped (DB unavailable): %s", exc)
            return
        raise
    if current != head_revision:
        raise RuntimeError(
            f"Database migration required: current={current}, head={head_revision}. "
            "Run `./migrate.sh' or `python -m app.migrate' before starting the API."
        )
    logger.info("Database schema verified at revision %s", head_revision)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """创建FastAPI应用生命周期上下文管理器"""
    # 0.重新初始化日志系统(uvicorn启动时dictConfig会影响根日志处理器，需要在此重新配置)
    setup_logging()
    configure_structured_logging()

    # 1.日志打印代码已经开始执行了
    logger.info("MyManus正在初始化")

    # 2.校验数据库迁移版本（迁移由独立 migrate job 执行）
    _verify_db_migrations()

    # 3.初始化Redis/Postgres/Cos客户端
    await get_redis().init()
    await get_postgres().init()
    await get_cos().init()

    # 4.种子化默认模型与内置Skill
    await bootstrap_data(
        uow_factory=get_uow,
        llm_model_service=get_llm_model_service(),
        skill_service=get_skill_service(),
    )
    sandbox_cleanup_task = asyncio.create_task(_sandbox_cleanup_loop())
    logger.info("MyManus初始化完成")

    try:
        # 4.lifespan分界点
        yield
    finally:
        sandbox_cleanup_task.cancel()
        try:
            await sandbox_cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            # 5.等待agent服务关闭
            logger.info("MyManus正在关闭")
            await asyncio.wait_for(get_agent_service().shutdown(), timeout=30.0)
            logger.info("Agent服务成功关闭")
        except asyncio.TimeoutError:
            logger.warning("Agent服务关闭超时, 强制关闭, 部分任务将被释放")
        except Exception as e:
            logger.error(f"Agent服务关闭期间出现错误: {str(e)}")

        # 6.关闭其他应用
        await get_redis().shutdown()
        await get_postgres().shutdown()
        await get_cos().shutdown()

        logger.info("Manus应用关闭成功")


# 4.创建MyManus应用实例
app = FastAPI(
    title="MyManus通用智能体",
    description="MyManus是一个通用的AI Agent系统，可以完全私有部署，使用A2A+MCP连接Agent/Tool，同时支持在沙箱中运行各种内置工具和操作",
    lifespan=lifespan,
    openapi_tags=openapi_tags,
    version="1.0.0",
)

# 5.配置CORS中间件，解决跨域问题
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 6.注册错误处理器
register_exception_handlers(app)

# 7.集成路由
app.include_router(well_known_router)
app.include_router(a2a_router, prefix="/api/a2a")
app.include_router(router, prefix="/api")
setup_observability(app)
