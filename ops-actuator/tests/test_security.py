"""Mirrors ops-collector/tests/test_security.py's transport/denial patterns,
adapted for the actuator's write-target allowlist (namespace + registered
workload + replica bounds) instead of the collector's probe registries.
"""

import pytest
from opencitadel_ops_actuator.actuator import Actuator
from opencitadel_ops_actuator.config import ActuatorSettings
from opencitadel_ops_actuator.main import build_parser
from opencitadel_ops_actuator.security import (
    TargetDenied,
    require_namespace,
    require_replicas_in_bounds,
    require_workload,
)


def test_default_transport_is_streamable_http():
    assert ActuatorSettings().transport == "streamable-http"
    assert build_parser().parse_args([]).transport is None


def test_namespace_allowlist_fails_closed(actuator_settings):
    settings = actuator_settings()
    with pytest.raises(TargetDenied, match="NAMESPACE_DENIED"):
        require_namespace(settings, "other")


def test_unregistered_workload_denied_by_security_helper(actuator_settings):
    settings = actuator_settings()
    with pytest.raises(TargetDenied, match="TARGET_DENIED"):
        require_workload(settings, "demo", "unknown")


def test_replicas_out_of_bounds_denied_by_security_helper(actuator_settings):
    settings = actuator_settings()
    target = require_workload(settings, "demo", "api")
    with pytest.raises(TargetDenied, match="REPLICAS_OUT_OF_BOUNDS"):
        require_replicas_in_bounds(target, 99)


def _unreachable_k8s():
    raise AssertionError("must not touch k8s")


@pytest.mark.asyncio
async def test_unregistered_namespace_denied_before_client(actuator_settings):
    actuator = Actuator(actuator_settings(), k8s_factory=_unreachable_k8s)
    result = await actuator.restart_workload("production", "api", "deployment", "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "NAMESPACE_DENIED"


@pytest.mark.asyncio
async def test_unregistered_workload_denied(actuator_settings):
    actuator = Actuator(actuator_settings(), k8s_factory=_unreachable_k8s)
    result = await actuator.restart_workload("demo", "unknown", "deployment", "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "TARGET_DENIED"


@pytest.mark.asyncio
async def test_scale_replicas_out_of_bounds_denied(actuator_settings):
    """scale 到注册 min/max 之外 -> denied before touching k8s (REPLICAS_OUT_OF_BOUNDS)."""
    actuator = Actuator(actuator_settings(), k8s_factory=_unreachable_k8s)
    result = await actuator.scale_workload("demo", "api", "deployment", 99, "key-1")
    assert result["action_outcome"] == "failed"
    assert result["error_code"] == "REPLICAS_OUT_OF_BOUNDS"
