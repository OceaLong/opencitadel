#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import BadRequestError
from app.domain.services.knowledge_base.url_guard import validate_public_url


def test_validate_public_url_rejects_file_scheme():
    with pytest.raises(BadRequestError):
        validate_public_url("file:///etc/passwd")


def test_validate_public_url_rejects_localhost():
    with pytest.raises(BadRequestError):
        validate_public_url("http://127.0.0.1/admin")


def test_validate_public_url_rejects_metadata_ip():
    with pytest.raises(BadRequestError):
        validate_public_url("http://169.254.169.254/latest/meta-data/")


def test_validate_public_url_accepts_public_https(monkeypatch):
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.url_guard.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    assert validate_public_url("https://example.com/docs") == "https://example.com/docs"
