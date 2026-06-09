#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Iterable

from app.application.services.marketplace.catalog import APP_IDS, MARKETPLACE_APPS


def catalog_app_ids() -> set[str]:
    return set(APP_IDS)


def validate_frontend_registry_ids(registry_ids: Iterable[str]) -> list[str]:
    """Return app ids present in backend catalog but missing from frontend registry."""
    backend = catalog_app_ids()
    frontend = set(registry_ids)
    return sorted(backend - frontend)


def validate_catalog_completeness() -> list[str]:
    """Return catalog entries missing required metadata fields."""
    required = {"id", "name", "description", "category", "tags", "examples"}
    missing: list[str] = []
    for app in MARKETPLACE_APPS:
        absent = required - set(app)
        if absent:
            missing.append(f"{app.get('id', '?')}: {sorted(absent)}")
    return missing
