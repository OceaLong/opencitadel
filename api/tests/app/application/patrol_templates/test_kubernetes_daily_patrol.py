from app.application.patrol_templates import load_patrol_template

EXPECTED = {
    "k8s-workload-availability",
    "k8s-restart-spike",
    "k8s-pending-failed",
    "k8s-warning-events",
    "k8s-resource-pressure",
    "app-error-rate",
    "tls-expiry",
    "backup-freshness",
    "dependency-health",
    "endpoint-health",
}

COMPOSE_EXPECTED = {
    "api-health",
    "api-status",
    "api-latency",
    "api-response-integrity",
    "console-health",
    "console-status",
    "console-latency",
    "console-response-integrity",
    "primary-dependencies",
    "console-connectivity",
}


def test_builtin_template_has_ten_stable_checks_and_no_raw_capabilities():
    pack = load_patrol_template("kubernetes-baseline-v1")
    assert {item.id for item in pack.checks} == EXPECTED
    assert len(pack.checks) == 10
    for check in pack.checks:
        assert not ({"url", "promql", "query", "command", "script"} & set(check.probe.args))
        assert len(check.probe.output_schema_hash) == 64


def test_compose_template_has_ten_real_registered_service_checks() -> None:
    pack = load_patrol_template("compose-services-baseline-v1")

    assert {item.id for item in pack.checks} == COMPOSE_EXPECTED
    assert len(pack.checks) == 10
    assert {item.probe.tool for item in pack.checks} == {
        "http_probe",
        "dependency_status",
    }
    assert {
        item.probe.args.get("probe_id") for item in pack.checks if item.probe.tool == "http_probe"
    } == {"primary-endpoint", "demo-console"}
    assert {
        item.probe.args.get("dependency_id")
        for item in pack.checks
        if item.probe.tool == "dependency_status"
    } == {"primary-dependencies", "demo-console-tcp"}
    for check in pack.checks:
        assert check.required_evidence == ["summary"]
        assert not ({"url", "promql", "query", "command", "script"} & set(check.probe.args))
        assert len(check.probe.output_schema_hash) == 64
