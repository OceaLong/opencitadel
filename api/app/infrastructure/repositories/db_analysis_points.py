"""Capture E11 scalar result series in the owning analysis/comparison transaction."""

import json
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.analysis.comparability import comparable_key
from app.domain.analysis.metrics import METRIC_VERSION
from app.domain.analysis.score_applicability import normalize_score_records, score_dimensions
from app.domain.evaluation.summary import EvaluationSnapshot
from app.infrastructure.execution.query_observation import named_query
from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository,
)
from app.infrastructure.repositories.db_evaluation_summary_repository import (
    DBEvaluationSummaryRepository,
)


async def points_operation(db, scope, principal, *, secret, kind, capture, operation, **payload):
    signed = await DBCurrentAuthority(db, signing_secret=secret).signed(
        scope, principal, operation=operation, capture_kind=kind, capture_id=capture, **payload
    )
    try:
        return await db.scalar(
            named_query(
                text("SELECT public.opencitadel_analysis_points(:body,:signature)"),
                "analysis.points",
            ),
            signed,
        )
    except DBAPIError as error:
        reason = str(error.orig)
        if "authorization" in reason:
            raise PermissionError("analysis_authorization_revoked") from None
        for code in (
            "analysis_capacity_exceeded",
            "analysis_accounting_capacity_exceeded",
            "analysis_refresh_required",
            "comparison_member_unavailable",
            "comparison_not_found",
        ):
            if code in reason:
                raise ValueError(code) from None
        raise


async def capture_points(db, scope, principal, *, secret, kind, capture, pins=None):
    prepared = await points_operation(
        db, scope, principal, secret=secret, kind=kind, capture=capture, operation="prepare"
    )
    if pins is not None:
        from app.domain.models.resource_pin import ResourceIdentity

        await pins.acquire(
            scope,
            "comparison_revision",
            capture,
            [ResourceIdentity.model_validate(item) for item in prepared["resources"]],
        )
    reader = DBEvaluationSummaryRepository(
        SimpleNamespace(db_session=db, evaluation_dataset=DBEvaluationDatasetRepository(db)),
        signing_secret=secret,
    )
    queries = {}
    for record in normalize_score_records(prepared["records"]):
        dimensions = score_dimensions(record)
        for source, dimension in dimensions:
            key = (
                record["batch_id"],
                record["evaluation_revision"],
                record["rubric"],
                source,
                dimension,
            )
            queries.setdefault(key, {})[record["result_id"]] = record
    total = 0
    for (batch, revision, rubric, source, dimension), records in queries.items():
        saved = await reader.capture(
            scope,
            principal,
            batch,
            source=source,
            dimension=dimension,
            rubric_id=rubric,
            evaluation_revision=revision,
        )
        snapshot = EvaluationSnapshot.model_validate(saved)
        batch_rows = []
        for row in snapshot.rows:
            record = records.get(str(row.id))
            if record is None:
                continue
            applicable = record["applicable"][source]
            identity = comparable_key(
                {
                    **record,
                    "metric_version": METRIC_VERSION,
                    "source": source,
                    "dimension": dimension,
                    "applicable_dimensions": applicable,
                }
            )
            score = next(
                (
                    item
                    for item in record["scores"]
                    if item["source"] == source and item["dimension"] == dimension
                ),
                None,
            )
            value = (
                score["value"]
                if score
                and score["status"] == "valid"
                and not score["invalidated"]
                and record["execution_status"] == "succeeded"
                else None
            )
            fixed = row.model_copy(
                update={"value": value, "invalidated": bool(score and score["invalidated"])}
            )
            batch_rows.append(
                {
                    "primary_run_id": record["run_id"],
                    "identity": identity,
                    "snapshot_id": str(snapshot.id),
                    "batch_id": batch,
                    "evaluation_revision": revision,
                    "source": source,
                    "dimension": dimension,
                    "rubric_id": rubric,
                    "captured_at": snapshot.captured_at.isoformat(),
                    "usage_watermark": snapshot.usage_watermark.isoformat(),
                    "row": fixed.model_dump(mode="json"),
                }
            )
        total += len(batch_rows)
        if total > 100000:
            raise ValueError("analysis_capacity_exceeded")
        # Bounded signed writes; no full trace/body is read or retained here.
        for offset in range(0, len(batch_rows), 100):
            rows = json.loads(json.dumps(batch_rows[offset : offset + 100], default=str))
            await points_operation(
                db,
                scope,
                principal,
                secret=secret,
                kind=kind,
                capture=capture,
                operation="store",
                records=rows,
            )
    return await points_operation(
        db, scope, principal, secret=secret, kind=kind, capture=capture, operation="read"
    )
