#!/usr/bin/env python
# -*- coding: utf-8 -*-
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """沙箱API服务基础配置信息"""
    log_level: str = "INFO"
    server_timeout_minutes: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "SERVER_TIMEOUT_MINUTES",
            "SERVICE_TIMEOUT_MINUTES",
            "server_timeout_minutes",
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
