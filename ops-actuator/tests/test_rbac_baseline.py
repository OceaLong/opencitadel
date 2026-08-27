"""Static least-privilege checks for the Actuator ServiceAccount."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
RBAC_FILES = [
    ROOT / "deploy" / "patrol-demo" / "manifests" / "actuator-rbac.yaml",
    ROOT / "deploy" / "kustomize" / "ops-actuator" / "rbac.yaml",
]
NETWORK_POLICY = ROOT / "deploy" / "kustomize" / "ops-actuator" / "network-policy.yaml"


@pytest.mark.parametrize("manifest", RBAC_FILES, ids=lambda path: path.parent.name)
def test_actuator_rbac_is_registered_write_verbs_only(manifest: Path):
    assert manifest.exists(), f"required RBAC manifest is missing: {manifest}"
    documents = [item for item in yaml.safe_load_all(manifest.read_text()) if item]
    roles = [item for item in documents if item.get("kind") in {"Role", "ClusterRole"}]
    assert roles
    allowed_verbs = {"get", "list", "watch", "patch"}
    forbidden_verbs = {"create", "delete", "deletecollection", "impersonate", "update"}
    forbidden_resources = {"secrets", "pods/exec", "pods/attach"}
    for role in roles:
        for rule in role.get("rules", []):
            verbs = set(rule.get("verbs", []))
            assert verbs <= allowed_verbs, f"unexpected verbs {verbs - allowed_verbs} in {manifest}"
            assert not (verbs & forbidden_verbs)
            assert not (set(rule.get("resources", [])) & forbidden_resources)


def test_actuator_network_policy_reaches_kubernetes_api_service_and_endpoint_ports():
    """Service traffic can be policy-evaluated before or after DNAT.

    Kubernetes exposes the API Service on 443, while kind's backing control
    plane endpoint listens on 6443. Both ports must remain allowed or the
    in-cluster writer can hang before it ever reaches its RBAC checks.
    """
    policy = yaml.safe_load(NETWORK_POLICY.read_text())
    unrestricted_destination_rules = [rule for rule in policy["spec"]["egress"] if "to" not in rule]
    allowed_tcp_ports = {
        int(port["port"])
        for rule in unrestricted_destination_rules
        for port in rule.get("ports", [])
        if port.get("protocol") == "TCP"
    }

    assert {443, 6443} <= allowed_tcp_ports
