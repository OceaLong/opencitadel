#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.application.services.app_config_repository_factory import create_app_config_repository
from app.infrastructure.repositories.db_app_config_repository import DbAppConfigRepository
from app.infrastructure.repositories.file_app_config_repository import FileAppConfigRepository


def test_create_app_config_repository_defaults_to_file(monkeypatch):
    monkeypatch.setattr(
        "app.application.services.app_config_repository_factory.get_settings",
        lambda: SimpleNamespace(use_db_app_config=False, app_config_filepath="config.yaml"),
    )
    repo = create_app_config_repository()
    assert isinstance(repo, FileAppConfigRepository)


def test_create_app_config_repository_uses_db_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "app.application.services.app_config_repository_factory.get_settings",
        lambda: SimpleNamespace(use_db_app_config=True, app_config_filepath="config.yaml"),
    )
    repo = create_app_config_repository()
    assert isinstance(repo, DbAppConfigRepository)
