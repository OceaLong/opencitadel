#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.marketplace.catalog import list_marketplace_apps


def test_all_catalog_apps_have_model_dependency():
    apps = list_marketplace_apps()
    assert apps
    for app in apps:
        assert app["model_dependency"] in {"none", "optional", "required"}, app["id"]


def test_offline_apps_marked_none():
    offline_ids = {"qr-generator", "dev-toolbox"}
    by_id = {a["id"]: a for a in list_marketplace_apps()}
    for app_id in offline_ids:
        assert by_id[app_id]["model_dependency"] == "none"
