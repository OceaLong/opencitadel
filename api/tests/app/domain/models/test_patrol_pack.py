from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.models.patrol import PatrolAssertion, PatrolPackConfig


def valid_pack() -> dict:
    return {
        "target_ref": "local",
        "timezone": "Asia/Shanghai",
        "scope": {"cluster": "demo", "namespaces": ["opencitadel"], "environment": "staging"},
        "checks": [
            {
                "id": "k8s-workload-availability",
                "title": "Availability",
                "probe": {"tool": "k8s_workload_summary", "args": {"namespace": "opencitadel"}, "output_schema_hash": "schema-v1"},
                "assertions": [{"id": "ready", "field": "$.unavailable_replicas", "op": "eq", "value": 0, "message": "not ready"}],
                "severity_on_fail": "critical",
                "required_evidence": ["summary", "resource_refs"],
            }
        ],
    }


def test_accepts_builtin_workload_check() -> None:
    assert PatrolPackConfig.model_validate(valid_pack()).checks[0].probe.tool == "k8s_workload_summary"


def test_rejects_duplicate_check_ids() -> None:
    data = valid_pack()
    data["checks"].append(dict(data["checks"][0]))
    with pytest.raises(ValidationError, match="unique"):
        PatrolPackConfig.model_validate(data)


def test_rejects_empty_enabled_checks() -> None:
    data = valid_pack()
    data["checks"][0]["enabled"] = False
    with pytest.raises(ValidationError, match="at least one"):
        PatrolPackConfig.model_validate(data)


@pytest.mark.parametrize("field", ["$..secret", "$[0]", "$.a[?(@.x)]", "secret"])
def test_rejects_unsafe_jsonpath(field: str) -> None:
    with pytest.raises(ValidationError, match="safe"):
        PatrolAssertion(id="x", field=field, op="eq", value=1, message="x")


def test_rejects_regex_longer_than_256() -> None:
    with pytest.raises(ValidationError, match="256"):
        PatrolAssertion(id="x", field="$.message", op="regex", value="x" * 257, message="x")


def test_rejects_unknown_required_evidence_type() -> None:
    data = valid_pack()
    data["checks"][0]["required_evidence"] = ["secret_dump"]
    with pytest.raises(ValidationError, match="unsupported"):
        PatrolPackConfig.model_validate(data)


def test_rejects_probe_tool_outside_allowlist() -> None:
    data = valid_pack()
    data["checks"][0]["probe"]["tool"] = "shell_execute"
    with pytest.raises(ValidationError):
        PatrolPackConfig.model_validate(data)
