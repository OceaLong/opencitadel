#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.utils.hitl import (
    domain_in_whitelist,
    matches_critical_action,
)


def test_domain_in_whitelist_exact_and_subdomain():
    assert domain_in_whitelist("ops-console", ["ops-console"])
    assert domain_in_whitelist("app.ops-console", ["ops-console"])
    assert not domain_in_whitelist("evil.com", ["ops-console"])


def test_matches_critical_close_action():
    assert matches_critical_action(
        "browser_click",
        {"selector": "#btn-confirm-close", "text": "Confirm close ticket"},
        ["close", "关单"],
    )


def test_skips_normal_click():
    assert not matches_critical_action(
        "browser_click",
        {"selector": "#btn-save-update", "text": "Save changes"},
        ["close", "refund", "delete"],
    )
