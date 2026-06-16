#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

import pytest

from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox


def _container_with_network_settings(network_settings: dict) -> MagicMock:
    container = MagicMock()
    container.attrs = {"NetworkSettings": network_settings}
    return container


def test_get_container_ip_custom_network_no_top_level_key():
    container = _container_with_network_settings({
        "Networks": {
            "manus-network": {"IPAddress": "172.18.0.5", "Gateway": "172.18.0.1"},
        },
    })
    ip = DockerSandbox._get_container_ip(container, preferred_network="manus-network")
    assert ip == "172.18.0.5"


def test_get_container_ip_prefers_configured_network():
    container = _container_with_network_settings({
        "Networks": {
            "bridge": {"IPAddress": "172.17.0.2"},
            "manus-network": {"IPAddress": "172.18.0.5"},
        },
    })
    ip = DockerSandbox._get_container_ip(container, preferred_network="manus-network")
    assert ip == "172.18.0.5"


def test_get_container_ip_legacy_default_bridge():
    container = _container_with_network_settings({
        "IPAddress": "172.17.0.2",
        "Networks": {},
    })
    ip = DockerSandbox._get_container_ip(container)
    assert ip == "172.17.0.2"


def test_get_container_ip_top_level_empty_string_fallback():
    container = _container_with_network_settings({
        "IPAddress": "",
        "Networks": {
            "manus-network": {"IPAddress": "172.18.0.9"},
        },
    })
    ip = DockerSandbox._get_container_ip(container, preferred_network="manus-network")
    assert ip == "172.18.0.9"


def test_get_container_ip_no_address_returns_none():
    container = _container_with_network_settings({"Networks": {}})
    assert DockerSandbox._get_container_ip(container) is None


def test_get_container_ip_missing_network_settings():
    container = MagicMock()
    container.attrs = {}
    assert DockerSandbox._get_container_ip(container) is None


def test_require_container_ip_raises_clear_error():
    container = _container_with_network_settings({"Networks": {}})
    with pytest.raises(RuntimeError, match="未分配到 IPv4"):
        DockerSandbox._require_container_ip(
            container,
            "manus-sandbox-abc123",
            preferred_network="manus-network",
        )
