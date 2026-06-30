#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event_upgrader import upgrade_event_payload


def test_error_event_upgrade_preserves_code():
    payload = {"type": "error", "error": "boom", "schema_version": 1}
    upgraded = upgrade_event_payload(payload)
    assert upgraded["type"] == "error"
    assert "code" in upgraded
    assert upgraded["code"] is None


def test_error_event_upgrade_keeps_existing_code():
    payload = {
        "type": "error",
        "error": "model down",
        "code": "MODEL_UNAVAILABLE",
        "schema_version": 2,
    }
    upgraded = upgrade_event_payload(payload)
    assert upgraded["code"] == "MODEL_UNAVAILABLE"
