"""Allowlisted typed file rows derived only from fixed safe source facts."""

from decimal import Decimal

from app.application.services.execution_export_encoding import ExportColumn
from app.domain.evaluation.summary_metrics import complete_cost

_BATCH_TYPES = {
    "result_id": "text",
    "case_id": "text",
    "case_label": "text",
    "config_id": "text",
    "config_label": "text",
    "repetition": "integer",
    "attempt": "integer",
    "run_id": "text",
    "run_revision": "integer",
    "result_revision": "integer",
    "attempts_json": "json",
    "score_run_id": "text",
    "score_run_revision": "integer",
    "score_result_revision": "integer",
    "execution_status": "text",
    "scoring_status": "text",
    "value": "score",
    "invalidated": "boolean",
    "value_missing": "boolean",
    "subject_usage_present": "boolean",
    "subject_calls": "integer",
    "subject_token_known": "integer",
    "subject_money_known": "integer",
    "subject_tokens": "integer",
    "subject_money_usd": "number",
    "subject_unresolved": "integer",
    "judge_usage_present": "boolean",
    "judge_calls": "integer",
    "judge_token_known": "integer",
    "judge_money_known": "integer",
    "judge_tokens": "integer",
    "judge_money_usd": "number",
    "judge_unresolved": "integer",
    "cost_usd": "number",
    "cost_complete": "boolean",
    "source": "text",
    "dimension": "text",
    "rubric_id": "text",
    "evaluation_revision": "integer",
    "usage_watermark": "text",
    "dataset_version": "text",
    "execution_mode": "text",
    "row_schema_version": "text",
}
BATCH_COLUMNS = tuple(ExportColumn(name, kind) for name, kind in _BATCH_TYPES.items())


def batch_row(row, metadata):
    """SummaryRow is immutable E11 capture data, metadata is the captured suite/series."""
    data = row.model_dump(mode="json")
    cost = complete_cost(row)
    result = {
        name: data[name]
        for name in (
            "case_id",
            "case_label",
            "config_id",
            "config_label",
            "repetition",
            "attempt",
            "run_id",
            "run_revision",
            "result_revision",
            "score_run_id",
            "score_run_revision",
            "score_result_revision",
            "execution_status",
            "scoring_status",
            "value",
            "invalidated",
        )
    }
    result.update(
        result_id=data["id"],
        attempts_json=data["attempts"],
        value_missing=row.value is None,
        cost_usd=cost,
        cost_complete=cost is not None,
        row_schema_version="execution-export-batch-v1",
    )
    for kind in ("subject", "judge"):
        usage = getattr(row, kind + "_usage")
        result[kind + "_usage_present"] = usage is not None
        for name in ("calls", "token_known", "money_known", "tokens", "unresolved"):
            result[kind + "_" + name] = getattr(usage, name) if usage is not None else None
        result[kind + "_money_usd"] = (
            Decimal(usage.money) if usage is not None and usage.money is not None else None
        )
    for name in (
        "source",
        "dimension",
        "rubric_id",
        "evaluation_revision",
        "usage_watermark",
        "dataset_version",
        "execution_mode",
    ):
        result[name] = metadata[name]
    return result


_RUN_TYPES = {
    "run_id": "text",
    "cut": "text",
    "family": "text",
    "purpose": "text",
    "execution_mode": "text",
    "configuration_revision": "text",
    "status": "text",
    "admitted_at": "text",
    "terminal_at": "text",
    "coverage_json": "json",
    "activity_occupancy_ms": "number",
    "tool_work_ms": "number",
    "approval_wait_ms": "number",
    "interval_coverage_json": "json",
    "usage_json": "json",
    "scores_json": "json",
    "missing_fields_json": "json",
    "row_schema_version": "text",
}
RUN_COLUMNS = tuple(ExportColumn(name, kind) for name, kind in _RUN_TYPES.items())


def run_row(fixed, *, cut):
    from dataclasses import asdict

    from app.domain.analysis.metrics import usage_metrics

    fact = fixed["run_fact"]
    interval = fixed.get("interval_fact") or {}
    approval = fixed.get("approval_fact") or {}
    row = {
        name: fact.get(name)
        for name in (
            "family",
            "purpose",
            "execution_mode",
            "status",
            "admitted_at",
            "terminal_at",
        )
    }
    row.update(
        run_id=fixed["run_id"],
        cut=cut,
        configuration_revision=fact.get("admission_configuration_id"),
        coverage_json={
            "state": fixed["coverage"].get("state"),
            "missing_fields": fixed["coverage"].get("missing_fields", []),
            "missing_intervals": [
                {name: interval.get(name) for name in ("start", "end", "reason")}
                for interval in fixed["coverage"].get("missing_intervals", [])
            ],
        },
        activity_occupancy_ms=interval.get("activity_occupancy_ms"),
        tool_work_ms=interval.get("tool_work_ms"),
        approval_wait_ms=approval.get("approval_wait_ms"),
        interval_coverage_json={
            name: interval.get(name)
            for name in (
                "activity_samples",
                "activity_missing",
                "tool_samples",
                "tool_missing",
                "tool_excluded",
            )
        },
        row_schema_version="execution-export-run-v1",
    )
    physical = [
        dict(
            call,
            cost_usd=Decimal(str(call["cost_usd"])) if call.get("cost_usd") is not None else None,
        )
        for call in fixed["usage"]
    ]
    row["usage_json"] = {
        purpose: {name: asdict(metric) for name, metric in metrics.items()}
        for purpose, metrics in usage_metrics(physical).items()
    }
    score_keys = (
        "result_id",
        "case_id",
        "config_id",
        "batch_id",
        "dataset_version",
        "mode",
        "environment_version",
        "rubric",
        "evaluation_revision",
        "required_conditions",
    )
    row["scores_json"] = []
    for score in fixed["scores"]:
        safe = {key: score.get(key) for key in score_keys}
        safe["source_sets"] = [
            {
                key: source.get(key)
                for key in (
                    "source",
                    "required_dimensions",
                    "applicable_dimensions",
                    "source_set_id",
                )
            }
            for source in score.get("source_sets", [])
        ]
        safe["scores"] = [
            {
                key: head.get(key)
                for key in (
                    "source",
                    "dimension",
                    "value",
                    "status",
                    "invalidated",
                    "source_set_id",
                )
            }
            for head in score.get("scores", [])
        ]
        row["scores_json"].append(safe)
    row["missing_fields_json"] = [name for name, value in row.items() if value is None]
    return row
