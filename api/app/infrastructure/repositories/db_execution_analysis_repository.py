"""Signed typed capture operations; runtime has no raw capture-table privileges."""

import asyncio
import json
import random
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.ports.execution_analysis import AnalysisCapture, AuthorityState, same_query
from app.domain.analysis.charts import chart_metrics
from app.domain.analysis.metrics import METRIC_VERSION, Metric, ratio, usage_metrics
from app.domain.analysis.point_series import point_series
from app.domain.analysis.score_summary import score_summary
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.query_observation import named_query
from app.infrastructure.repositories.db_analysis_charts import chart_facts
from app.infrastructure.repositories.db_analysis_points import capture_points, points_operation
from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority
from app.infrastructure.security.db_authorization import configure_session_authorization


class DBExecutionAnalysisRepository:
    def __init__(self, session_factory, *, signing_secret):
        self.session_factory, self.secret = session_factory, signing_secret

    @asynccontextmanager
    async def transaction(self, scope, principal, *, current=False):
        async with self.session_factory() as db:
            await db.rollback()
            await db.connection(
                execution_options={
                    "isolation_level": "READ COMMITTED" if current else "REPEATABLE READ"
                }
            )
            await configure_session_authorization(
                db,
                AuthorizationContext.for_principal(principal, scope=scope),
                signing_secret=self.secret,
            )
            try:
                yield db
                if not current:
                    await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _operation(self, db, scope, principal, operation, **payload):
        signed = await DBCurrentAuthority(db, signing_secret=self.secret).signed(
            scope, principal, operation=operation, **payload
        )
        try:
            return await db.scalar(
                named_query(
                    text("SELECT public.opencitadel_analysis_capture(:body,:signature)"),
                    "analysis.capture",
                ),
                signed,
            )
        except DBAPIError as exc:
            if "analysis_authorization" in str(exc.orig):
                raise PermissionError("analysis_authorization_revoked") from None
            for reason in (
                "analysis_refresh_required",
                "analysis_capacity_exceeded",
                "analysis_accounting_capacity_exceeded",
                "analysis_query_invalid",
            ):
                if reason in str(exc.orig):
                    raise ValueError(reason) from None
            raise

    async def run_page(self, scope, principal, capture, *, cursor=None, limit=50):
        from app.infrastructure.repositories.db_analysis_native import run_page

        return await run_page(self, scope, principal, capture, cursor=cursor, limit=limit)

    async def current(self, scope, principal, capture):
        async with self.transaction(scope, principal, current=True) as db:
            value = await self._operation(
                db, scope, principal, "current", capture_id=capture.watermark
            )
            points = await points_operation(
                db,
                scope,
                principal,
                secret=self.secret,
                kind="analysis",
                capture=capture.watermark,
                operation="current",
            )
            return AuthorityState(
                value["authority_revision"], value["manifest"] + ":" + points["fingerprint"]
            )

    async def capture(self, scope, principal, query, watermark):
        for attempt in range(5):
            try:
                return await self._capture_once(scope, principal, query, watermark)
            except DBAPIError as exc:
                code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                if code not in {"40001", "40P01"} or attempt == 4:
                    raise
                await asyncio.sleep(min(0.05 * 2**attempt, 0.4) * random.uniform(0.5, 1.5))
        raise RuntimeError("analysis_capture_retry_exhausted")

    async def _capture_once(self, scope, principal, query, watermark):
        encoded_query = json.loads(json.dumps(asdict(query), default=str))
        # SQL cache equality includes this marker; old immutable captures stay intact.
        encoded_query["metric_version"] = METRIC_VERSION
        async with self.transaction(scope, principal) as db:
            if watermark is not None:
                value = await self._operation(db, scope, principal, "read", capture_id=watermark)
                if (
                    not same_query(value["query"], encoded_query)
                    or value["metrics"].get("metric_version") != METRIC_VERSION
                ):
                    raise ValueError("analysis_refresh_required")
                points = await points_operation(
                    db,
                    scope,
                    principal,
                    secret=self.secret,
                    kind="analysis",
                    capture=value["watermark"],
                    operation="current",
                )
                return AnalysisCapture(
                    value["watermark"],
                    AuthorityState(
                        value["authority_revision"],
                        value["manifest"] + ":" + points["captured_fingerprint"],
                    ),
                    value["metrics"],
                )
            value = await self._operation(db, scope, principal, "begin", query=encoded_query)
            if "metrics" in value:
                if value["metrics"].get("metric_version") != METRIC_VERSION:
                    raise ValueError("analysis_refresh_required")
                points = await points_operation(
                    db,
                    scope,
                    principal,
                    secret=self.secret,
                    kind="analysis",
                    capture=value["watermark"],
                    operation="current",
                )
                return AnalysisCapture(
                    value["watermark"],
                    AuthorityState(
                        value["authority_revision"],
                        value["manifest"] + ":" + points["captured_fingerprint"],
                    ),
                    value["metrics"],
                )
            value["chart_facts"] = await chart_facts(
                db,
                scope,
                principal,
                signing_secret=self.secret,
                operation="live",
                capture_id=value["watermark"],
            )
            points = await capture_points(
                db,
                scope,
                principal,
                secret=self.secret,
                kind="analysis",
                capture=value["watermark"],
            )
            metrics = self._metrics(value, query)
            metrics["evaluation_series"] = point_series(points["records"])
            sealed = await self._operation(
                db,
                scope,
                principal,
                "seal",
                capture_id=value["watermark"],
                manifest=value["manifest"],
                metrics=metrics,
            )
            return AnalysisCapture(
                sealed["watermark"],
                AuthorityState(
                    sealed["authority_revision"],
                    sealed["manifest"] + ":" + points["captured_fingerprint"],
                ),
                metrics,
            )

    @staticmethod
    def _metrics(value, query, *, allow_automatic_comparison=True):
        series = []
        for row in value["run_groups"]:
            completed, failed, total = row["completed"], row["failed"], row["run_count"]
            metrics = {
                k: asdict(Metric(row[k], "run", sample_count=total))
                for k in ("run_count", "completed", "failed", "cancelled", "pending", "unknown")
            }
            metrics["success_rate"] = asdict(
                ratio(completed, completed + failed, excluded=total - completed - failed)
            )
            for name in ("latency_p50", "latency_p95"):
                metrics[name] = asdict(
                    Metric(
                        row[name],
                        "ms",
                        sample_count=row["latency_samples"],
                        missing_count=row["latency_missing"],
                        excluded_count=total - completed - failed,
                    )
                )
            series.append(
                {
                    "group": {
                        key: row[key]
                        for key in (
                            "family",
                            "purpose",
                            "execution_mode",
                            "configuration_revision",
                            "bucket",
                        )
                    },
                    "metrics": metrics,
                }
            )
        physical = value["physical"]
        for call in physical:
            if call["cost_usd"] is not None:
                call["cost_usd"] = Decimal(call["cost_usd"])
        usage = {
            purpose: {name: asdict(metric) for name, metric in metrics.items()}
            for purpose, metrics in usage_metrics(physical).items()
        }
        intervals = []
        group_fields = ("family", "purpose", "execution_mode", "configuration_revision", "bucket")
        for row in value["intervals"]:
            metrics = {
                "activity_occupancy_ms": asdict(
                    Metric(
                        row["activity_occupancy_ms"],
                        "ms",
                        sample_count=row["activity_samples"],
                        missing_count=row["activity_missing"],
                    )
                ),
                "tool_work_ms": asdict(
                    Metric(
                        row["tool_work_ms"],
                        "ms",
                        sample_count=row["tool_samples"],
                        missing_count=row["tool_missing"],
                    )
                ),
                "tool_error_rate": asdict(
                    ratio(row["tool_errors"], row["tool_terminal"], excluded=row["tool_excluded"])
                ),
            }
            for name in (
                "tool_unknown",
                "tool_deferred",
                "tool_cancelled",
                "tool_execution_errors",
                "tool_business_errors",
            ):
                metrics[name] = asdict(
                    Metric(
                        row[name],
                        "attempt",
                        sample_count=row["tool_terminal"] + row["tool_excluded"],
                    )
                )
            intervals.append({"group": {k: row[k] for k in group_fields}, "metrics": metrics})
        approvals = [
            {
                "group": {k: row[k] for k in group_fields},
                "metrics": {
                    "approval_wait_ms": asdict(
                        Metric(
                            row["approval_wait_ms"],
                            "ms",
                            sample_count=row["samples"],
                            missing_count=row["missing"],
                        )
                    )
                },
            }
            for row in value["approvals"]
        ]
        body = {
            "metric_version": METRIC_VERSION,
            "coverage": value["coverage"],
            "captured_at": value["captured_at"],
            "limits": {
                "primary_members": 100000,
                "accounting_members": 1000000,
                "active_captures_per_caller": 20,
                "capture_ttl_seconds": 900,
            },
            "series": series,
            "intervals": intervals,
            "approvals": approvals,
            "usage": {
                "grain": dict(query.filters).get("accounting", "run"),
                "accounting_run_count": value["accounting_run_count"],
                "purposes": usage,
            },
            "allocations": value["allocations"],
            "scores": score_summary(
                value["score_records"],
                selection=query.comparison_config_version_ids,
                allow_automatic=allow_automatic_comparison,
            ),
        }
        if "chart_facts" in value:
            body["charts"] = chart_metrics(value["chart_facts"])
        return json.loads(json.dumps(body, default=str, allow_nan=False))

    @staticmethod
    async def cleanup_expired(db, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_cleanup_limit")
        return await db.scalar(
            text("SELECT public.opencitadel_analysis_cleanup(:limit)"), {"limit": limit}
        )
