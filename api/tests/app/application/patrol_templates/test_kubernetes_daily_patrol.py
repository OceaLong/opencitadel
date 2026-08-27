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


def test_builtin_template_has_ten_stable_checks_and_no_raw_capabilities():
    pack = load_patrol_template("kubernetes-baseline-v1")
    assert {item.id for item in pack.checks} == EXPECTED
    assert len(pack.checks) == 10
    for check in pack.checks:
        assert not ({"url", "promql", "query", "command", "script"} & set(check.probe.args))
        assert len(check.probe.output_schema_hash) == 64
