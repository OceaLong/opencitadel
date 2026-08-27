from datetime import UTC, datetime

from opencitadel_ops_collector.contracts import CollectorEnvelope, CollectorErrorCode, evidence_for


def test_error_enum_is_complete():
    assert {item.value for item in CollectorErrorCode} == {
        "AUTH_FAILED",
        "TARGET_NOT_FOUND",
        "NAMESPACE_DENIED",
        "PROBE_DENIED",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "OUTPUT_TOO_LARGE",
        "REDACTION_FAILED",
        "SCHEMA_MISMATCH",
        "RATE_LIMITED",
        "INTERNAL_ERROR",
    }


def test_response_envelope_contains_trace_source_and_evidence_contract():
    data = {"healthy": True}
    item = CollectorEnvelope(
        target_ref="demo",
        duration_ms=3,
        status="ok",
        data=data,
        evidence=evidence_for("demo", data, ["summary"]),
    )
    assert item.request_id
    assert item.collected_at.tzinfo is not None
    assert item.evidence[0].target_ref == "demo"
    assert len(item.evidence[0].sha256) == 64
    assert item.evidence[0].expires_at > datetime.now(UTC)
