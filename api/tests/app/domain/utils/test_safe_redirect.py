#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.utils.safe_redirect import resolve_safe_redirect_path


@pytest.mark.parametrize(
    ("redirect", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/invitations/abc", "/invitations/abc"),
        ("/invitations/abc?foo=bar", "/invitations/abc?foo=bar"),
        ("//evil.com", "/"),
        ("https://evil.com", "/"),
        ("evil.com", "/"),
        ("/../admin", "/../admin"),
    ],
)
def test_resolve_safe_redirect_path(redirect, expected):
    assert resolve_safe_redirect_path(redirect) == expected
