"""Static RBAC scan for the actuator's ServiceAccount, mirroring the scan
approach in ops-collector/tests/integration/test_golden_fixtures.py's
test_collector_rbac_is_get_list_watch_only, adjusted for the actuator's
registered write verb (patch) in addition to get/list/watch.

The actuator RBAC manifests are produced by Task 5 of this remediation plan
(deploy/patrol-demo/manifests/actuator-rbac.yaml and
deploy/kustomize/ops-actuator/rbac.yaml) and now exist, so this scan runs
for real rather than skipping.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
RBAC_FILES = [
    ROOT / "deploy" / "patrol-demo" / "manifests" / "actuator-rbac.yaml",
    ROOT / "deploy" / "kustomize" / "ops-actuator" / "rbac.yaml",
]


@pytest.mark.parametrize("manifest", RBAC_FILES, ids=lambda path: path.parent.name)
def test_actuator_rbac_is_registered_write_verbs_only(manifest: Path):
    assert manifest.exists(), f"{manifest} is required (Task 5 of this remediation plan)"
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
