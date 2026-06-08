#!/usr/bin/env python
# -*- coding: utf-8 -*-
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MyManus后端中控配置信息，从.env或者环境变量中加载数据"""

    # 项目基础配置
    env: str = "development"
    log_level: str = "INFO"
    app_config_filepath: str = "config.yaml"
    api_key_secret: str = "my-manus-api-key-secret-change-in-production"

    # 记忆配置
    memory_recall_limit: int = 20
    memory_auto_extract_enabled: bool = True
    memory_vector_enabled: bool = False
    memory_compact_strategy: str = "hybrid"  # rule | llm | hybrid
    memory_compact_token_threshold: int = 32000
    memory_compact_keep_recent: int = 12
    memory_compact_tool_content_max_chars: int = 2000
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    tool_timeout_seconds: int = 120

    # 数据库相关配置
    sqlalchemy_database_uri: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/manus"

    # Redis缓存配置
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    redis_stream_maxlen: int = 10000

    # Cos腾讯云对象存储配置
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_scheme: str = "https"
    cos_bucket: str = ""
    cos_domain: str = ""

    # Sandbox配置
    sandbox_address: Optional[str] = None
    sandbox_image: Optional[str] = None
    sandbox_name_prefix: Optional[str] = None
    sandbox_ttl_minutes: Optional[int] = 60
    sandbox_network: Optional[str] = None
    sandbox_chrome_args: Optional[str] = ""
    sandbox_https_proxy: Optional[str] = None
    sandbox_http_proxy: Optional[str] = None
    sandbox_no_proxy: Optional[str] = None
    sandbox_cleanup_interval_seconds: int = 300

    # Observability
    otel_enabled: bool = False
    otel_service_name: str = "my-manus-api"
    otel_exporter_endpoint: str = ""
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Secrets / Vault
    vault_addr: str = ""
    vault_token: str = ""
    use_db_app_config: bool = False

    # 使用pydantic v2的写法来完成环境变量信息的告知
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取当前MyManus项目的配置信息，并对内容进行缓存，避免重复读取"""
    settings = Settings()
    return settings
