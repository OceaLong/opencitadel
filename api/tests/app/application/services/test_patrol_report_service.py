from uuid import uuid4

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_report_service import PatrolReportService
from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFindingSeverity,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
)


def test_report_has_six_fixed_sections_is_deterministic_and_redacts_secrets():
    config = load_patrol_template("kubernetes-baseline-v1")
    config.checks = config.checks[:1]
    run = PatrolRun(
        pack_id="pack-1",
        execution_run_id=uuid4(),
        pack_version=1,
        pack_snapshot={"name": "Daily", "config": config.model_dump(mode="json")},
        trigger_type=PatrolTriggerType.MANUAL,
        status=PatrolRunStatus.COMPLETED,
        idempotency_key="trigger-1",
        collector_capability_hash="c" * 64,
        pass_count=1,
        evidence_completeness=1,
    )
    result = PatrolCheckResult(
        run_id=run.id,
        check_id=config.checks[0].id,
        status=PatrolCheckStatus.PASS,
        severity=PatrolFindingSeverity.INFO,
        observed={"password": "do-not-render", "authorization": "Bearer abc.def.ghi"},
        explanation="token=super-secret",
        fingerprint="f" * 64,
    )

    first = PatrolReportService.render(run, [result], [])
    second = PatrolReportService.render(run, [result], [])

    assert first == second
    for heading in (
        "执行摘要",
        "需要处理",
        "全部检查结果",
        "与上次运行的变化",
        "证据与审计",
        "限制与缺失数据",
    ):
        assert f"## {heading}" in first
    assert "do-not-render" not in first
    assert "super-secret" not in first
    assert "***REDACTED***" in first
