#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for the domain-level runtime config port.

Covers the fail-fast contract: calling get_runtime_config() before any
provider has registered must raise RuntimeError (no silent defaults),
registering a provider must make get_runtime_config() transparently
delegate to it, and re-registering must make the latest provider win.
"""
import pytest

from app.domain import config_port


@pytest.fixture(autouse=True)
def _reset_provider():
    """Isolate each test from module-level provider registration state."""
    original = config_port._provider
    config_port._provider = None
    yield
    config_port._provider = original


def test_get_runtime_config_without_registration_raises_runtime_error():
    with pytest.raises(RuntimeError):
        config_port.get_runtime_config()


def test_get_runtime_config_delegates_to_registered_provider():
    sentinel = object()
    config_port.register_runtime_config_provider(lambda: sentinel)

    assert config_port.get_runtime_config() is sentinel


def test_register_runtime_config_provider_last_registration_wins():
    config_port.register_runtime_config_provider(lambda: "first")
    config_port.register_runtime_config_provider(lambda: "second")

    assert config_port.get_runtime_config() == "second"
