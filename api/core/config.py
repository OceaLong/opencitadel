#!/usr/bin/env python
# -*- coding: utf-8 -*-
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_LOCAL_URI = "postgresql+asyncpg://postgres:postgres@localhost:5432/manus"


class Settings(BaseSettings):
    """启动引导与密钥配置，从 .env 或环境变量加载。行为类配置见 config.yaml。"""

    # 项目基础
    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    app_config_filepath: str = "config.yaml"
    api_key_secret: str = "my-manus-api-key-secret-change-in-production"

    # 数据库连接（引导层，启动前必须可用）
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "manus"
    postgres_host: str = "localhost"
    sqlalchemy_database_uri: str = ""
    sqlalchemy_echo: bool = False
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 5
    postgres_pool_recycle_seconds: int = 1800

    # Redis 连接
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    # 腾讯云 COS 密钥与桶
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_scheme: str = "https"
    cos_bucket: str = ""
    cos_domain: str = ""

    # 嵌入 / 可观测性密钥
    embedding_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # 应用配置存储：false=本地 config.yaml，true=PostgreSQL app_configs 表
    use_db_app_config: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def derive_sqlalchemy_database_uri(self) -> "Settings":
        uri = (self.sqlalchemy_database_uri or "").strip()
        if not uri or uri == _DEFAULT_LOCAL_URI:
            user = quote_plus(self.postgres_user)
            password = quote_plus(self.postgres_password)
            object.__setattr__(
                self,
                "sqlalchemy_database_uri",
                f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:5432/{self.postgres_db}",
            )
        return self


def sqlalchemy_sync_database_uri(settings: Settings | None = None) -> str:
    """Return sync SQLAlchemy URL (psycopg2) from bootstrap settings."""
    settings = settings or get_settings()
    return settings.sqlalchemy_database_uri.replace("+asyncpg", "+psycopg2")


@lru_cache()
def get_settings() -> Settings:
    """获取启动引导配置（进程内缓存）。"""
    return Settings()
