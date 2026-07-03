#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.integration_server_service import (
    _apply_masked_secret_updates,
    _apply_masked_url_update,
)


def test_apply_masked_url_update_preserves_existing_when_masked():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    updated = "https://mcp.amap.com/mcp?key=3244****2424"
    assert _apply_masked_url_update(updated, existing) == existing


def test_apply_masked_url_update_uses_new_value_when_not_masked():
    existing = "https://mcp.amap.com/mcp?key=old-key-value"
    updated = "https://mcp.amap.com/mcp?key=new-key-value"
    assert _apply_masked_url_update(updated, existing) == updated


def test_apply_masked_secret_updates_per_key():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"API_KEY": "****", "OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret", "OTHER": "new"}
