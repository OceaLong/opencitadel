import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import DeploymentSettings

logger = logging.getLogger(__name__)


def ensure_rls_capable_role(
    *,
    env: str,
    role_name: str,
    is_superuser: bool,
    bypasses_rls: bool,
) -> None:
    """Fail closed when the production application role can bypass RLS."""
    if env.lower() != "production":
        return
    if is_superuser or bypasses_rls:
        raise RuntimeError(
            f"PostgreSQL role[{role_name}] can bypass row-level security; "
            "configure a NOSUPERUSER NOBYPASSRLS application role"
        )


class Postgres:
    """Postgres数据库基础类，用于完成数据库连接等配置操作"""

    def __init__(self, settings: DeploymentSettings) -> None:
        """构造函数，完成postgres数据库引擎、会话工厂的创建"""
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None
        self._settings = settings

    async def init(self) -> None:
        """初始化postgres连接"""
        # 1.判断是否已经创建好引擎，如果连上了则中断程序
        if self._engine is not None:
            logger.warning("Postgres引擎已初始化，无需重复操作")
            return

        try:
            # 2.创建异步引擎
            logger.info("正在初始化Postgres连接...")
            self._engine = create_async_engine(
                self._settings.sqlalchemy_database_uri,
                echo=self._settings.sqlalchemy_echo,
                pool_pre_ping=True,  # 每次从连接池获取连接前先检测连接是否有效，防止使用已关闭的连接
                pool_size=self._settings.postgres_pool_size,
                max_overflow=self._settings.postgres_max_overflow,
                pool_recycle=self._settings.postgres_pool_recycle_seconds,
            )

            # 3.创建会话工厂
            self._session_factory = async_sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self._engine,
                info={
                    "database_authorization_signing_secret": self._settings.session_secret,
                },
            )
            logger.info("Postgres会话工厂创建完毕")

            # 4.连接Postgres并执行预操作
            async with self._engine.begin() as async_conn:
                role_result = await async_conn.execute(
                    text(
                        """
                        SELECT current_user, rolsuper, rolbypassrls
                        FROM pg_roles
                        WHERE rolname = current_user
                        """
                    )
                )
                role = role_result.one()
                ensure_rls_capable_role(
                    env=self._settings.env,
                    role_name=str(role[0]),
                    is_superuser=bool(role[1]),
                    bypasses_rls=bool(role[2]),
                )
                # 5.检查是否安装了uuid扩展，如果没有的话则安装
                await async_conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
                logger.info("成功连接Postgres并安装uuid-ossp扩展")
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("连接Postgres失败: %s", e)
            raise

    async def shutdown(self) -> None:
        """关闭Postgres连接"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("成功关闭Postgres连接")

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """只读属性，返回已初始化的会话工厂"""
        if self._session_factory is None:
            raise RuntimeError("Postgres未初始化，请先调用init()函数初始化")
        return self._session_factory
