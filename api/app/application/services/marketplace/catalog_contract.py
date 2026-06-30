#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Iterable

from app.application.services.marketplace.catalog import APP_IDS, MARKETPLACE_APPS, enrich_marketplace_app


def catalog_app_ids() -> set[str]:
    return set(APP_IDS)


def validate_frontend_registry_ids(registry_ids: Iterable[str]) -> list[str]:
    """Return app ids present in backend catalog but missing from frontend registry."""
    backend = catalog_app_ids()
    frontend = set(registry_ids)
    return sorted(backend - frontend)


def validate_catalog_completeness() -> list[str]:
    """Return catalog entries missing required metadata fields."""
    required = {"id", "name", "description", "category", "tags", "examples", "model_dependency"}
    missing: list[str] = []
    for app in MARKETPLACE_APPS:
        enriched = enrich_marketplace_app(app)
        absent = required - set(enriched)
        if absent:
            missing.append(f"{app.get('id', '?')}: {sorted(absent)}")
    return missing
