#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.marketplace.catalog import APP_IDS
from app.application.services.marketplace.catalog_contract import (
    validate_catalog_completeness,
    validate_frontend_registry_ids,
)

# Keep in sync with ui/src/components/marketplace/app-registry.tsx meta ids.
FRONTEND_REGISTRY_IDS = {
    "video-search",
    "nutrition-analysis",
    "consumption-calculator",
    "document-qa",
    "smart-translation",
    "prompt-lab",
    "qr-generator",
    "dev-toolbox",
    "secret-generator",
    "document-converter",
    "watermark-tool",
    "party-room",
    "questionnaire",
    "personality-tests",
    "fortune-teller",
    "unit-converter",
}


def test_backend_catalog_has_complete_metadata():
    assert validate_catalog_completeness() == []


def test_frontend_registry_covers_backend_catalog():
    missing = validate_frontend_registry_ids(FRONTEND_REGISTRY_IDS)
    assert missing == [], f"frontend missing apps: {missing}"


def test_backend_catalog_ids_match_frontend_registry():
    assert APP_IDS == FRONTEND_REGISTRY_IDS
