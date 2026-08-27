"""Governance metrics: counters/histograms must accept the spec'd labels and
increment monotonically. Uses REGISTRY sample-value diffs (not absolutes) so
test order/parallel pollution across the module never flips these assertions.
"""

from prometheus_client import REGISTRY

from app.infrastructure.observability.governance_metrics import (
    observe_approval_decision_seconds,
    record_chain_verification,
    record_policy_denial,
    record_remediation_transition,
    record_tool_execution,
)


def _counter_value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _histogram_count(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(f"{name}_count", labels) or 0.0


def test_observe_approval_decision_seconds_increments_histogram_count():
    before = _histogram_count("governance_approval_decision_seconds", {})
    observe_approval_decision_seconds(12.5)
    after = _histogram_count("governance_approval_decision_seconds", {})
    assert after - before == 1.0


def test_record_policy_denial_increments_counter_by_layer_and_tool():
    labels = {"layer": "execution", "tool": "shell_exec"}
    before = _counter_value("governance_policy_denials_total", labels)
    record_policy_denial("execution", "shell_exec")
    after = _counter_value("governance_policy_denials_total", labels)
    assert after - before == 1.0


def test_record_tool_execution_increments_counter_and_observes_duration():
    counter_labels = {"tool": "web_search", "status": "ok"}
    before_count = _counter_value("governance_tool_executions_total", counter_labels)
    before_hist = _histogram_count("governance_tool_execution_seconds", {"tool": "web_search"})
    record_tool_execution("web_search", "ok", 1.25)
    after_count = _counter_value("governance_tool_executions_total", counter_labels)
    after_hist = _histogram_count("governance_tool_execution_seconds", {"tool": "web_search"})
    assert after_count - before_count == 1.0
    assert after_hist - before_hist == 1.0


def test_record_tool_execution_with_none_seconds_skips_histogram_observe():
    counter_labels = {"tool": "capability_x", "status": "denied"}
    before_count = _counter_value("governance_tool_executions_total", counter_labels)
    before_hist = _histogram_count("governance_tool_execution_seconds", {"tool": "capability_x"})
    record_tool_execution("capability_x", "denied", None)
    after_count = _counter_value("governance_tool_executions_total", counter_labels)
    after_hist = _histogram_count("governance_tool_execution_seconds", {"tool": "capability_x"})
    assert after_count - before_count == 1.0
    assert after_hist - before_hist == 0.0


def test_record_remediation_transition_increments_counter_by_status():
    before = _counter_value("governance_remediation_transitions_total", {"to_status": "executed"})
    record_remediation_transition("executed")
    after = _counter_value("governance_remediation_transitions_total", {"to_status": "executed"})
    assert after - before == 1.0


def test_record_chain_verification_increments_counter_by_result():
    before = _counter_value("governance_audit_chain_verifications_total", {"result": "intact"})
    record_chain_verification("intact")
    after = _counter_value("governance_audit_chain_verifications_total", {"result": "intact"})
    assert after - before == 1.0
