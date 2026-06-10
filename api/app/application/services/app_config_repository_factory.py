#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Factory for AppConfigRepository implementations."""
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.infrastructure.repositories.db_app_config_repository import DbAppConfigRepository
from app.infrastructure.repositories.file_app_config_repository import FileAppConfigRepository
from core.config import get_settings


def create_app_config_repository() -> AppConfigRepository:
    settings = get_settings()
    if settings.use_db_app_config:
        return DbAppConfigRepository()
    return FileAppConfigRepository(settings.app_config_filepath)
