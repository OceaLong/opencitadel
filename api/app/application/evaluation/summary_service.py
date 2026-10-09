"""Current authority on every page; costs remain fixed to a short-lived captured cut."""

import base64
import hashlib
import hmac
import json
from uuid import UUID

from app.domain.evaluation.summary import EvaluationSnapshot, SummaryPage
from app.domain.evaluation.summary_metrics import derive_snapshot
from app.domain.models.authorization import AuthorizationContext


class SummaryService:
    def __init__(self, suites):
        self.suites = suites

    def _cursor(self, context, position):
        body = json.dumps([context, position], separators=(",", ":")).encode()
        return (
            base64.urlsafe_b64encode(
                body + hmac.new(self.suites.cursor_secret, body, hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )

    def _position(self, cursor, context):
        try:
            if len(cursor) > 4096:
                raise ValueError()
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            body, mac = raw[:-32], raw[-32:]
            saved, position = json.loads(body)
            if saved != context or not hmac.compare_digest(
                mac, hmac.new(self.suites.cursor_secret, body, hashlib.sha256).digest()
            ):
                raise ValueError()
            return position
        except (ValueError, TypeError, KeyError):
            raise ValueError("invalid_cursor") from None

    @staticmethod
    def _context(scope, principal, *query):
        return [scope.model_dump(mode="json"), principal.user_id, *query]

    async def summary(
        self,
        scope,
        principal,
        batch_id,
        *,
        source,
        dimension,
        rubric_id=None,
        result_id=None,
        evaluation_revision=None,
        cursor=None,
        limit=100,
    ):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = self._context(
            scope,
            principal,
            "summary",
            str(batch_id),
            source,
            dimension,
            str(rubric_id) if rubric_id else None,
            evaluation_revision,
            str(result_id) if result_id else None,
        )
        position = self._position(cursor, context) if cursor else None
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            batch = await work.evaluation_batch.get(scope, batch_id)
            if position:
                saved = await work.evaluation_summary.get(scope, principal, batch_id, position[0])
                offset = position[1]
            else:
                if rubric_id is None:
                    suite = await work.evaluation_configuration.get_version(
                        scope, "suite", batch["suite_version"]
                    )
                    rubric_id = UUID(suite["rubric_version"])
                saved = await work.evaluation_summary.capture(
                    scope,
                    principal,
                    batch_id,
                    source=source,
                    dimension=dimension,
                    rubric_id=rubric_id,
                    evaluation_revision=evaluation_revision,
                )
                offset = 0
                await work.commit()
        snapshot = EvaluationSnapshot.model_validate(saved)
        if type(offset) is not int or not 0 <= offset <= 5000:
            raise ValueError("invalid_cursor")
        end = offset + limit
        derived = derive_snapshot(snapshot)
        if offset != 0:
            derived["points"] = ()
        return SummaryPage(
            snapshot_id=snapshot.id,
            batch_id=batch_id,
            captured_at=snapshot.captured_at,
            usage_watermark=snapshot.usage_watermark,
            expires_at=snapshot.expires_at,
            evaluation_revision=snapshot.evaluation_revision,
            source=snapshot.source,
            dimension=snapshot.dimension,
            rubric_id=snapshot.rubric_id,
            items=snapshot.rows[offset:end],
            selected_result=next((row for row in snapshot.rows if row.id == result_id), None),
            next_cursor=self._cursor(context, [str(snapshot.id), end])
            if end < len(snapshot.rows)
            else None,
            **derived,
        )

    async def list(self, scope, principal, *, cursor=None, limit=50):
        context = self._context(scope, principal, "batches")
        after = self._position(cursor, context) if cursor else None
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            rows = await work.evaluation_summary.list_batches(scope, after=after, limit=limit + 1)
            return {
                "items": [dict(row) for row in rows[:limit]],
                "next_cursor": self._cursor(context, str(rows[limit - 1]["id"]))
                if len(rows) > limit
                else None,
            }

    async def events(self, scope, principal, batch_id, *, cursor=None):
        context = self._context(scope, principal, "batch-events", str(batch_id))
        after = self._position(cursor, context) if cursor else 0
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            await work.evaluation_batch.get(scope, batch_id)
            rows = await work.evaluation_summary.events(scope, batch_id, after=after)
            return [
                {
                    "cursor": self._cursor(context, row["revision"]),
                    "revision": row["revision"],
                    "kind": row["kind"],
                }
                for row in rows
            ]
