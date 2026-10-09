"""Signed export capture and durable fenced lifecycle; no raw fact-table grants."""

import asyncio
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.ports.execution_analysis import AnalysisQuery
from app.application.ports.execution_comparison import ComparisonRequest
from app.application.ports.execution_export import ExportChunk, ExportLease
from app.application.services.execution_export_encoding import safe_json
from app.application.services.execution_export_rows import RUN_COLUMNS, run_row
from app.domain.analysis.metrics import METRIC_VERSION
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority
from app.infrastructure.repositories.db_execution_analysis_repository import (
    DBExecutionAnalysisRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class DBExecutionExportRepository:
    def __init__(self, session_factory, *, signing_secret):
        self.session_factory, self.secret = session_factory, signing_secret
        self.transactions = DBExecutionAnalysisRepository(
            session_factory, signing_secret=signing_secret
        )

    async def _signed(self, db, scope, principal, operation, **payload):
        return await DBCurrentAuthority(db, signing_secret=self.secret).signed(
            scope, principal, operation=operation, **payload
        )

    async def _operation(self, db, scope, principal, operation, **payload):
        if operation == "accept":
            await db.execute(text("SET LOCAL statement_timeout = '30s'"))
        signed = await self._signed(db, scope, principal, operation, **payload)
        if operation == "accept":
            request = payload["request"]
            source = {"body": None, "signature": None}
            if request.get("source_kind") == "filter":
                parsed = ComparisonRequest.parse(
                    {**request["selection"], "request_id": str(uuid4()), "detail_run_ids": []}
                )
                source = await self._signed(
                    db,
                    scope,
                    principal,
                    "materialize",
                    **json.loads(json.dumps(asdict(parsed), default=str)),
                )
            signed.update(source_encoded=source["body"], source_signature=source["signature"])
            sql = "SELECT public.opencitadel_export_accept(:body,:signature,:source_encoded,:source_signature)"
        else:
            sql = "SELECT public.opencitadel_export_jobs(:body,:signature)"
        try:
            return await db.scalar(text(sql), signed)
        except DBAPIError as error:
            message = str(error.orig)
            if "analysis_authorization" in message:
                raise PermissionError("export_authorization_revoked") from None
            for code in (
                "export_not_found",
                "export_request_conflict",
                "export_quota_exceeded",
                "export_capacity_exceeded",
                "export_capture_mismatch",
                "export_lease_lost",
                "export_not_ready",
                "export_download_lease_lost",
                "export_snapshot_unavailable",
                "export_authorization_changed",
                "invalid_export_request",
                "comparison_not_found",
            ):
                if code in message:
                    raise ValueError(code) from None
            raise

    async def accept(self, scope, principal, request):
        for attempt in range(3):
            try:
                async with (
                    asyncio.timeout(120),
                    self.transactions.transaction(scope, principal) as db,
                ):
                    accepted = await self._operation(
                        db,
                        scope,
                        principal,
                        "accept",
                        request=request,
                        owner_scope=scope.model_dump(mode="json")
                        if isinstance(scope, OwnerScope)
                        else scope,
                    )
                    if accepted.get("replayed"):
                        return accepted
                    return await self._seal(db, scope, principal, accepted, request)
            except DBAPIError as error:
                code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
                if code in {"40001", "40P01"} and attempt < 2:
                    continue
                reason = str(error.orig)
                if "analysis_authorization" in reason or "summary_authorization" in reason:
                    raise PermissionError("export_authorization_revoked") from None
                for stable in (
                    "export_snapshot_unavailable",
                    "export_capacity_exceeded",
                    "export_request_conflict",
                    "export_quota_exceeded",
                    "export_capture_mismatch",
                    "comparison_capacity_exceeded",
                    "analysis_accounting_capacity_exceeded",
                    "comparison_member_unavailable",
                    "comparison_not_found",
                    "analysis_query_invalid",
                ):
                    if stable in reason:
                        raise ValueError(stable) from None
                raise
        raise RuntimeError("export_retry_exhausted")

    async def _copy(self, db, scope, principal, accepted):
        signed = await self._signed(
            db,
            scope,
            principal,
            "copy",
            export_id=accepted["id"],
            capture_id=accepted["capture_id"],
        )
        source = await self._signed(
            db,
            scope,
            principal,
            "read",
            comparison_id=accepted["comparison_id"],
            revision=accepted["revision"],
            detail_run_ids=[],
            limit=1,
        )
        return await db.scalar(
            text(
                "SELECT public.opencitadel_export_copy(:body,:signature,:read_encoded,:read_signature)"
            ),
            {**signed, "read_encoded": source["body"], "read_signature": source["signature"]},
        )

    async def _seal(self, db, scope, principal, accepted, request):
        if request.get("source_kind") == "batch":
            return await self._seal_batch(db, scope, principal, accepted, request)
        value = await self._copy(db, scope, principal, accepted)
        query = value["query"]
        parsed = AnalysisQuery(
            datetime.fromisoformat(query["start"]),
            datetime.fromisoformat(query["end"]),
            query["grain"],
            query["timezone"],
            tuple(tuple(f) for f in query["filters"]),
            tuple(query.get("comparison_config_version_ids", [])),
        )
        metrics = DBExecutionAnalysisRepository._metrics(value["facts"], parsed)
        metrics["accounting_coverage"] = value["facts"]["accounting_coverage"]
        metadata = {
            "export_id": accepted["id"],
            "schema_version": "execution-export-v1",
            "table_kind": "runs",
            "source_kind": request["source_kind"],
            "source_id": request.get("comparison_id"),
            "source_revision": request.get("revision"),
            "evaluation_revision": None,
            "source_snapshot_id": None,
            "captured_at": value["body"]["captured_at"],
            "usage_watermark": None,
            "data_watermark": accepted["id"],
            "timezone": query["timezone"],
            "grain": query["grain"],
            "metric_version": METRIC_VERSION,
            "filters": dict(query["filters"]),
            "execution_modes": sorted(
                {
                    group["execution_mode"]
                    for group in value["facts"]["run_groups"]
                    if group.get("execution_mode")
                }
            ),
            "score_series": None,
            "coverage": value["facts"]["coverage"],
            "data_row_count": value["row_count"],
            "accounting_grain": dict(query["filters"]).get("accounting", "run"),
            "expires_at": value["expires_at"],
            "column_types": {c.name: c.kind for c in RUN_COLUMNS},
            "null_semantics": "Missing observations remain null; missing scores are not zero.",
        }
        header = json.loads(
            safe_json(
                {
                    "format": request["format"],
                    "metadata": metadata,
                    "metrics": metrics,
                    "columns": [asdict(c) for c in RUN_COLUMNS],
                }
            )
        )
        return await self._operation(
            db, scope, principal, "seal", export_id=accepted["id"], header=header
        )

    async def _seal_batch(self, db, scope, principal, accepted, request):
        from types import SimpleNamespace

        from pydantic import TypeAdapter

        from app.application.services.execution_export_rows import BATCH_COLUMNS
        from app.domain.evaluation.summary import EvaluationSnapshot
        from app.domain.evaluation.summary_metrics import derive_snapshot
        from app.infrastructure.repositories.db_evaluation_dataset_repository import (
            DBEvaluationDatasetRepository,
        )
        from app.infrastructure.repositories.db_evaluation_summary_repository import (
            DBEvaluationSummaryRepository,
        )

        summary = DBEvaluationSummaryRepository(
            SimpleNamespace(db_session=db, evaluation_dataset=DBEvaluationDatasetRepository(db)),
            signing_secret=self.secret,
        )
        if request.get("snapshot_id"):
            snapshot = await summary.get(
                scope, principal, request["batch_id"], request["snapshot_id"]
            )
        else:
            snapshot = await summary.capture(
                scope,
                principal,
                request["batch_id"],
                source=request["source"],
                dimension=request["dimension"],
                rubric_id=request["rubric_id"],
                evaluation_revision=request["evaluation_revision"],
            )
        snapshot_id = snapshot["id"]
        signed = await self._signed(
            db, scope, principal, "batch_context", export_id=accepted["id"], snapshot_id=snapshot_id
        )
        context = await db.scalar(
            text("SELECT public.opencitadel_export_batch_context(:body,:signature)"), signed
        )
        query = AnalysisQuery.parse(
            {"start": context["start"], "end": context["end"]}, "day", request["timezone"]
        )
        source_payload = {
            "query": json.loads(json.dumps(asdict(query), default=str)),
            "mode": "explicit",
            "run_ids": context["run_ids"],
            "excluded_run_ids": [],
            "detail_run_ids": [],
            "baseline_configuration": None,
            "request_id": str(uuid4()),
        }
        source = await self._signed(db, scope, principal, "materialize", **source_payload)
        staged = await db.scalar(
            text(
                "SELECT public.opencitadel_export_stage(:body,:signature,:source_encoded,:source_signature)"
            ),
            {**signed, "source_encoded": source["body"], "source_signature": source["signature"]},
        )
        await self._copy(db, scope, principal, staged)
        signed = await self._signed(
            db, scope, principal, "batch_promote", export_id=accepted["id"], snapshot_id=snapshot_id
        )
        context = await db.scalar(
            text("SELECT public.opencitadel_export_batch_promote(:body,:signature)"), signed
        )
        snapshot = EvaluationSnapshot.model_validate(context["snapshot"])
        metrics = TypeAdapter(dict).dump_python(derive_snapshot(snapshot), mode="json")
        metadata = {
            "export_id": accepted["id"],
            "schema_version": "execution-export-v1",
            "table_kind": "batch_results",
            "source_kind": "batch",
            "source_id": request["batch_id"],
            "source_revision": None,
            "evaluation_revision": snapshot.evaluation_revision,
            "source_snapshot_id": str(snapshot.id),
            "captured_at": snapshot.captured_at.isoformat(),
            "usage_watermark": snapshot.usage_watermark.isoformat(),
            "data_watermark": accepted["id"],
            "timezone": request["timezone"],
            "grain": "case_result",
            "metric_version": "evaluation-series-v1",
            "filters": {},
            "execution_modes": [context["execution_mode"]],
            "score_series": {
                "source": snapshot.source,
                "dimension": snapshot.dimension,
                "rubric_id": str(snapshot.rubric_id),
            },
            "coverage": "complete",
            "data_row_count": len(snapshot.rows),
            "accounting_grain": "case_result",
            "expires_at": context["expires_at"],
            "column_types": {c.name: c.kind for c in BATCH_COLUMNS},
            "null_semantics": "Missing observations remain null; missing scores are not zero.",
        }
        row_context = {
            "source": snapshot.source,
            "dimension": snapshot.dimension,
            "rubric_id": str(snapshot.rubric_id),
            "evaluation_revision": snapshot.evaluation_revision,
            "usage_watermark": snapshot.usage_watermark.isoformat(),
            "dataset_version": context["dataset_version"],
            "execution_mode": context["execution_mode"],
        }
        return await self._operation(
            db,
            scope,
            principal,
            "seal",
            export_id=accepted["id"],
            header={
                "format": request["format"],
                "metadata": metadata,
                "metrics": metrics,
                "columns": [asdict(c) for c in BATCH_COLUMNS],
                "row_context": row_context,
            },
        )

    async def _call(self, scope, principal, operation, **payload):
        async with self.transactions.transaction(scope, principal, current=True) as db:
            result = await self._operation(db, scope, principal, operation, **payload)
            await db.commit()
        if (
            operation != "status"
            and isinstance(result, dict)
            and result.get("status") in {"expired", "invalidated"}
        ):
            raise ValueError("export_" + result["status"])
        return result

    async def _kernel(self, operation, *, target=None, token=None, reason=None):
        async with self.session_factory() as db:
            await configure_session_authorization(
                db, AuthorizationContext.system("execution-kernel"), signing_secret=self.secret
            )
            result = await db.scalar(
                text("SELECT public.opencitadel_export_kernel(:operation,:target,:token,:reason)"),
                {"operation": operation, "target": target, "token": token, "reason": reason},
            )
            await db.commit()
            return result

    async def claim(self):
        value = await self._kernel("claim")
        if value is None:
            return None
        return ExportLease(
            value["export_id"],
            value["token"],
            OwnerScope.model_validate(value["scope"]),
            Principal.model_validate(value["principal"]),
            datetime.fromisoformat(value["expires_at"]),
        )

    async def current(self, scope, principal, export_id):
        await self._call(scope, principal, "current", export_id=export_id)

    async def get(self, scope, principal, export_id):
        return await self._call(scope, principal, "status", export_id=export_id)

    async def _lease(self, lease, operation, **payload):
        return await self._call(
            lease.scope,
            lease.principal,
            operation,
            export_id=lease.export_id,
            lease_token=lease.token,
            **payload,
        )

    async def renew(self, lease):
        await self._lease(lease, "renew")

    async def header(self, lease):
        return await self._lease(lease, "header")

    async def page(self, lease, *, after, limit=200):
        from app.application.services.execution_export_rows import batch_row
        from app.domain.evaluation.summary import SummaryRow

        page = await self._lease(lease, "page", after=after, limit=limit)
        rows = []
        size = 2
        for fixed in page["rows"]:
            if page.get("row_context"):
                row = batch_row(SummaryRow.model_validate(fixed), page["row_context"])
            else:
                cut = hmac.new(
                    self.secret.encode(),
                    b"export-cut:"
                    + safe_json([lease.export_id, fixed["run_id"], fixed["cut"]]).encode(),
                    hashlib.sha256,
                ).hexdigest()
                row = run_row(fixed, cut=cut)
            size += len(safe_json(row).encode()) + (1 if rows else 0)
            if size > 1024 * 1024:
                if not rows:
                    raise ValueError("export_capacity_exceeded")
                return {"rows": rows, "next_after": after + len(rows)}
            rows.append(row)
        return {"rows": rows, "next_after": page["next_after"]}

    async def begin_chunk(self, lease, *, ordinal, size, digest):
        return ExportChunk(
            **await self._lease(lease, "begin_chunk", ordinal=ordinal, size=size, digest=digest)
        )

    @asynccontextmanager
    async def chunk_io(self, lease, chunk):
        # Keep one checked-out connection and only the object transaction lock
        # through I/O. Rolling back the guard savepoint releases its job row lock.
        async with self.transactions.transaction(lease.scope, lease.principal, current=True) as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "export-object:" + chunk.intent_id},
            )
            guard = await db.begin_nested()
            try:
                result = await self._operation(
                    db,
                    lease.scope,
                    lease.principal,
                    "chunk_guard",
                    export_id=lease.export_id,
                    lease_token=lease.token,
                    intent_id=chunk.intent_id,
                )
            finally:
                await guard.rollback()
            if result.get("status") in {"expired", "invalidated"}:
                raise PermissionError("export_" + result["status"])
            yield

    async def publish(self, lease, chunks, *, size, digest):
        await self._lease(
            lease, "publish", chunks=[asdict(c) for c in chunks], size=size, digest=digest
        )

    async def write_completed(self, lease, chunk):
        # Completion must not become visible between cleanup's delete and
        # cleaned statements. Use its object lock for this entire transaction.
        async with self.session_factory() as db:
            await configure_session_authorization(
                db, AuthorizationContext.system("execution-kernel"), signing_secret=self.secret
            )
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "export-object:" + chunk.intent_id},
            )
            await db.scalar(
                text(
                    "SELECT public.opencitadel_export_kernel('write_completed',:target,:token,NULL)"
                ),
                {"target": chunk.intent_id, "token": lease.token},
            )
            await db.commit()

    async def fail(self, lease, *, code):
        await self._kernel("fail", target=lease.export_id, token=lease.token, reason=code)

    async def cleanup(self, objects):
        import asyncio

        items = await self._kernel("cleanup_inventory")
        removed = 0
        for item in items:
            async with self.session_factory() as db:
                await configure_session_authorization(
                    db, AuthorizationContext.system("execution-kernel"), signing_secret=self.secret
                )
                locked = await db.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": "export-object:" + item["id"]},
                )
                if not locked:
                    await db.rollback()
                    continue
                sql = text("SELECT public.opencitadel_export_kernel(:operation,:target,NULL,NULL)")
                guard = await db.begin_nested()
                try:
                    proof = await db.scalar(
                        sql, {"operation": "cleanup_guard", "target": item["id"]}
                    )
                finally:
                    await guard.rollback()
                if proof["eligible"]:
                    async with asyncio.timeout(30):
                        await objects.delete_bytes(proof["key"])
                    await db.scalar(sql, {"operation": "cleaned", "target": item["id"]})
                    removed += 1
                await db.commit()
        await self._kernel("retire_captures")
        return removed
