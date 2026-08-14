#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for the domain-level vector-memory port.

Covers the fail-fast contract: calling get_vector_memory() before any
provider has registered must raise RuntimeError (no silent no-op embedder),
registering a provider must make get_vector_memory() transparently
delegate to it, and re-registering must make the latest provider win.
"""
import pytest

from app.domain import vector_port


@pytest.fixture(autouse=True)
def _reset_provider():
    """Isolate each test from module-level provider registration state."""
    original = vector_port._provider
    vector_port._provider = None
    yield
    vector_port._provider = original


def test_get_vector_memory_without_registration_raises_runtime_error():
    with pytest.raises(RuntimeError):
        vector_port.get_vector_memory()


def test_get_vector_memory_delegates_to_registered_provider():
    sentinel = object()
    vector_port.register_vector_memory_provider(lambda: sentinel)

    assert vector_port.get_vector_memory() is sentinel


def test_register_vector_memory_provider_last_registration_wins():
    vector_port.register_vector_memory_provider(lambda: "first")
    vector_port.register_vector_memory_provider(lambda: "second")

    assert vector_port.get_vector_memory() == "second"
