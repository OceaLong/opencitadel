"""Retained E11 point/matrix adaptation, without a second score or cost formula."""

import json
from collections import defaultdict
from datetime import datetime, timedelta

from app.domain.analysis.metrics import METRIC_VERSION
from app.domain.evaluation.summary import EvaluationSnapshot, SummaryRow
from app.domain.evaluation.summary_metrics import derive_snapshot


def point_series(records):
    if records is None:
        return None
    grouped = defaultdict(dict)
    metadata = {}
    for item in records:
        if len(item["identity"]) != 9 or item["identity"][5] != METRIC_VERSION:
            raise ValueError("analysis_metric_version_unavailable")
        key = (
            tuple(item["identity"][:-1]),
            tuple(item["identity"][-1]),
            item["batch_id"],
            item["evaluation_revision"],
            item["source"],
            item["dimension"],
            item["rubric_id"],
        )
        row = SummaryRow.model_validate(item["row"])
        grouped[key][row.id] = row
        metadata[key] = item
    result = []
    for key, rows in grouped.items():
        meta = metadata[key]
        captured = datetime.fromisoformat(meta["captured_at"])
        snapshot = EvaluationSnapshot(
            id=meta["snapshot_id"],
            batch_id=meta["batch_id"],
            captured_at=captured,
            usage_watermark=meta["usage_watermark"],
            expires_at=captured + timedelta(minutes=15),
            evaluation_revision=meta["evaluation_revision"],
            source=meta["source"],
            dimension=meta["dimension"],
            rubric_id=meta["rubric_id"],
            rows=tuple(rows.values()),
            allocations=(),
        )
        derived = derive_snapshot(snapshot)
        result.append(
            {
                "identity": meta["identity"],
                "batch_id": meta["batch_id"],
                "evaluation_revision": meta["evaluation_revision"],
                "source": meta["source"],
                "dimension": meta["dimension"],
                "rubric_id": meta["rubric_id"],
                "captured_at": meta["captured_at"],
                "usage_watermark": meta["usage_watermark"],
                "cost_basis": "case_result_subject_plus_judge",
                "rows": [row.model_dump(mode="json") for row in rows.values()],
                "points": [point.model_dump(mode="json") for point in derived["points"]],
                "distribution_metadata": derived["distribution_metadata"],
                "quality_cost_metadata": derived["quality_cost_metadata"],
                "scoring_counts": derived["scoring_counts"],
                "subject_usage": derived["subject_usage"],
                "judge_usage": derived["judge_usage"],
            }
        )
    return json.loads(json.dumps(result, default=str))
