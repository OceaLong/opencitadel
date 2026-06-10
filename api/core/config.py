#!/usr/bin/env python
# -*- coding: utf-8 -*-
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """启动引导与密钥配置，从 .env 或环境变量加载。行为类配置见 config.yaml。"""

    # 项目基础
    env: str = "development"
    log_level: str = "INFO"
    app_config_filepath: str = "config.yaml"
    api_key_secret: str = "my-manus-api-key-secret-change-in-production"

    # 数据库连接（引导层，启动前必须可用）
    sqlalchemy_database_uri: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/manus"
    sqlalchemy_echo: bool = False
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20
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

    # Vault（可选）
    vault_addr: str = ""
    vault_token: str = ""

    # 应用配置存储：false=本地 config.yaml，true=PostgreSQL app_configs 表
    use_db_app_config: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取启动引导配置（进程内缓存）。"""
    return Settings()
