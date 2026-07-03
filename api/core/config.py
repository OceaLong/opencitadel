#!/usr/bin/env python
# -*- coding: utf-8 -*-
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_LOCAL_URI = "postgresql+asyncpg://postgres:postgres@localhost:5432/opencitadel"


class Settings(BaseSettings):
    """启动引导与密钥配置，从 .env 或环境变量加载。行为类配置见 config.yaml。"""

    # 项目基础
    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    app_config_filepath: str = "config.yaml"
    api_key_secret: str = "opencitadel-api-key-secret-change-in-production"
    jwt_secret: str = "opencitadel-jwt-secret-change-in-production"
    session_secret: str = "opencitadel-session-secret-change-in-production"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    cookie_domain: str = ""
    cookie_secure: bool = False
    oauth_redirect_base: str = "http://localhost:8088/api/auth/oauth"
    frontend_base_url: str = "http://localhost:3000"
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = ""

    # 数据库连接（引导层，启动前必须可用）
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "opencitadel"
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

    # 对象存储：cos（默认）或 minio
    storage_provider: str = "cos"

    # 腾讯云 COS 密钥与桶
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_scheme: str = "https"
    cos_bucket: str = ""
    cos_domain: str = ""

    # MinIO（STORAGE_PROVIDER=minio 时生效）
    minio_endpoint: str = "opencitadel-minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "opencitadel"
    minio_secure: bool = False
    minio_public_endpoint: str = ""

    # 嵌入 / 可观测性密钥
    embedding_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # 应用配置存储：false=本地 config.yaml，true=PostgreSQL app_configs 表
    use_db_app_config: bool = True

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
        if self.env.lower() == "production":
            insecure_values = {
                "api_key_secret": "opencitadel-api-key-secret-change-in-production",
                "jwt_secret": "opencitadel-jwt-secret-change-in-production",
                "session_secret": "opencitadel-session-secret-change-in-production",
            }
            for field, default in insecure_values.items():
                if getattr(self, field) == default:
                    raise ValueError(f"{field} must be changed in production")
            if not self.cookie_secure:
                raise ValueError("cookie_secure must be true in production")
            if not self.bootstrap_admin_password:
                raise ValueError("bootstrap_admin_password must be set in production")
        return self


def sqlalchemy_sync_database_uri(settings: Settings | None = None) -> str:
    """Return sync SQLAlchemy URL (psycopg2) from bootstrap settings."""
    settings = settings or get_settings()
    return settings.sqlalchemy_database_uri.replace("+asyncpg", "+psycopg2")


@lru_cache()
def get_settings() -> Settings:
    """获取启动引导配置（进程内缓存）。"""
    return Settings()
