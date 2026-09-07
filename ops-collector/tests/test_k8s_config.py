"""Kubernetes configuration works both in-cluster and in disposable local CI."""

from unittest.mock import patch

import pytest
from kubernetes.config.config_exception import ConfigException
from opencitadel_ops_collector.k8s_client import KubernetesReader


def test_local_reader_falls_back_to_selected_context(monkeypatch):
    monkeypatch.setenv("PATROL_DEMO_CONTEXT", "kind-opencitadel-patrol-test")
    with (
        patch(
            "kubernetes.config.load_incluster_config", side_effect=ConfigException("outside pod")
        ),
        patch("kubernetes.config.load_kube_config") as local,
    ):
        KubernetesReader()
    local.assert_called_once_with(context="kind-opencitadel-patrol-test")


def test_incluster_reader_does_not_load_local_credentials():
    with (
        patch("kubernetes.config.load_incluster_config"),
        patch("kubernetes.config.load_kube_config") as local,
    ):
        KubernetesReader()
    local.assert_not_called()


def test_invalid_local_configuration_remains_an_error():
    with (
        patch(
            "kubernetes.config.load_incluster_config", side_effect=ConfigException("outside pod")
        ),
        patch("kubernetes.config.load_kube_config", side_effect=ConfigException("invalid context")),
        pytest.raises(ConfigException, match="invalid context"),
    ):
        KubernetesReader()
