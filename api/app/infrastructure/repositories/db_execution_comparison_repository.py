"""Typed signed durable comparison operations with no raw private-fact privileges."""

import base64
import hashlib
import hmac
import json
from dataclasses import asdict, replace
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.ports.execution_analysis import AnalysisQuery, AuthorityState
from app.application.ports.execution_comparison import ComparisonRead
from app.domain.analysis.point_series import point_series
from app.domain.models.resource_pin import ResourceIdentity
from app.infrastructure.repositories.db_analysis_charts import chart_facts
from app.infrastructure.repositories.db_analysis_points import capture_points, points_operation
from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority
from app.infrastructure.repositories.db_execution_analysis_repository import (
    DBExecutionAnalysisRepository,
)
from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository


def comparison_owner_validator(principal, *, signing_secret):
    async def validate(db, scope, owner_id):
        if principal is None:
            return False
        signed = await DBCurrentAuthority(db, signing_secret=signing_secret).signed(
            scope, principal, operation="owner", revision_id=owner_id
        )
        value = await db.scalar(
            text("SELECT public.opencitadel_comparison_control(:body,:signature)"), signed
        )
        return bool(value and value.get("valid"))

    return validate


class DBExecutionComparisonRepository:
    def __init__(self, session_factory, *, signing_secret):
        self.session_factory, self.secret = session_factory, signing_secret
        self.transactions = DBExecutionAnalysisRepository(
            session_factory, signing_secret=signing_secret
        )
        self.cipher = Fernet(
            base64.urlsafe_b64encode(
                hashlib.sha256(("comparison-cursor-v1:" + signing_secret).encode()).digest()
            )
        )

    async def _operation(self, db, scope, principal, function, operation, **payload):
        if function not in {"materialize", "control", "read", "jobs"}:
            raise ValueError("invalid_comparison_operation")
        signed = await DBCurrentAuthority(db, signing_secret=self.secret).signed(
            scope, principal, operation=operation, **payload
        )
        try:
            return await db.scalar(
                text(f"SELECT public.opencitadel_comparison_{function}(:body,:signature)"), signed
            )
        except DBAPIError as exc:
            original = str(exc.orig)
            if "analysis_authorization" in original or "comparison_read_only" in original:
                raise PermissionError("comparison_authorization_revoked") from None
            for reason in (
                "comparison_request_conflict",
                "invalid_comparison_request_id",
                "comparison_not_found",
                "comparison_conflict",
                "alignment_conflict",
                "comparison_member_unavailable",
                "comparison_retention_unavailable",
                "comparison_capacity_exceeded",
                "comparison_step_limit",
                "comparison_artifact_unavailable",
                "comparison_diff_lease_lost",
                "comparison_diff_capacity",
                "comparison_diff_output_limit",
                "comparison_baseline_unavailable",
                "invalid_comparison_request",
                "invalid_comparison_alignment",
                "analysis_accounting_capacity_exceeded",
            ):
                if reason in original:
                    raise ValueError(reason) from None
            raise

    async def materialize(
        self, scope, principal, request, *, comparison_id=None, expected_revision=None
    ):
        if principal.is_auditor:
            raise PermissionError("comparison_read_only_principal")
        payload = json.loads(json.dumps(asdict(request), default=str))
        for attempt in range(3):
            try:
                async with self.transactions.transaction(scope, principal) as db:
                    value = await self._operation(
                        db,
                        scope,
                        principal,
                        "materialize",
                        "materialize",
                        **payload,
                        comparison_id=comparison_id,
                        expected_revision=expected_revision,
                    )
                    if value.get("replayed"):
                        return value["comparison_id"], value["revision"]
                    pins = DBResourcePinRepository(
                        db,
                        owner_validators={
                            "comparison_revision": comparison_owner_validator(
                                principal, signing_secret=self.secret
                            )
                        },
                    )
                    await pins.acquire(
                        scope,
                        "comparison_revision",
                        value["revision_id"],
                        [
                            ResourceIdentity.model_validate(resource)
                            for resource in value["resources"]
                        ],
                    )
                    await capture_points(
                        db,
                        scope,
                        principal,
                        secret=self.secret,
                        kind="comparison",
                        capture=value["revision_id"],
                        pins=pins,
                    )
                    await self._operation(
                        db, scope, principal, "control", "publish", revision_id=value["revision_id"]
                    )
                    return value["comparison_id"], value["revision"]
            except DBAPIError as exc:
                code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                if code not in {"40001", "40P01"} or attempt == 2:
                    raise
        raise RuntimeError("comparison_retry_exhausted")

    def _cursor(self, scope, principal, comparison_id, revision, after):
        return self.cipher.encrypt(
            json.dumps(
                {
                    "scope": scope.model_dump(mode="json"),
                    "caller": principal.user_id,
                    "comparison_id": comparison_id,
                    "revision": revision,
                    "after": after,
                },
                sort_keys=True,
            ).encode()
        ).decode()

    def _after(self, scope, principal, comparison_id, revision, cursor):
        if cursor is None:
            return -1
        try:
            if not isinstance(cursor, str) or len(cursor) > 4096:
                raise ValueError
            value = json.loads(self.cipher.decrypt(cursor.encode()))
            if any(
                value.get(k) != v
                for k, v in {
                    "scope": scope.model_dump(mode="json"),
                    "caller": principal.user_id,
                    "comparison_id": comparison_id,
                    "revision": revision,
                }.items()
            ):
                raise ValueError
            after = value["after"]
            if type(after) is not int or not 0 <= after < 100000:
                raise ValueError
            return after
        except (ValueError, KeyError, InvalidToken, UnicodeError):
            raise ValueError("invalid_comparison_cursor") from None

    async def read(
        self,
        scope,
        principal,
        comparison_id,
        revision,
        *,
        cursor=None,
        limit=100,
        detail_run_ids=(),
    ):
        after = self._after(scope, principal, comparison_id, revision, cursor)
        async with self.transactions.transaction(scope, principal) as db:
            value = await self._operation(
                db,
                scope,
                principal,
                "read",
                "read",
                comparison_id=comparison_id,
                revision=revision,
                after=after,
                limit=limit,
                detail_run_ids=list(detail_run_ids),
            )
            value["facts"]["chart_facts"] = await chart_facts(
                db,
                scope,
                principal,
                signing_secret=self.secret,
                operation="comparison",
                capture_id=value["body"]["revision_id"],
            )
            points = await points_operation(
                db,
                scope,
                principal,
                secret=self.secret,
                kind="comparison",
                capture=value["body"]["revision_id"],
                operation="read",
            )
            from app.infrastructure.repositories.db_analysis_native import comparison_context

            native = await comparison_context(
                db,
                scope,
                principal,
                secret=self.secret,
                capture_id=value["body"]["revision_id"],
                run_ids=[member["run_id"] for member in value["body"]["members"]],
            )
        query = value["query"]
        parsed = AnalysisQuery(
            datetime.fromisoformat(query["start"]),
            datetime.fromisoformat(query["end"]),
            query["grain"],
            query["timezone"],
            tuple(tuple(f) for f in query["filters"]),
            tuple(query.get("comparison_config_version_ids", [])),
            query.get("start_explicit", True),
            query.get("end_explicit", True),
        )
        allow_automatic = not parsed.comparison_config_version_ids
        visible_configs = {r["config_id"] for r in value["facts"]["score_records"]}
        parsed = replace(
            parsed,
            comparison_config_version_ids=tuple(
                c for c in parsed.comparison_config_version_ids if c in visible_configs
            ),
        )
        body = value["body"]
        body["context"] = {
            "start": query["start"],
            "end": query["end"],
            "grain": query["grain"],
            "timezone": query["timezone"],
            "filters": dict(query["filters"]),
            "selection_mode": native["selection_mode"],
            "detail_run_ids": native["detail_run_ids"],
        }
        if query.get("comparison_config_version_ids"):
            body["context"]["filters"]["comparison_config_version_ids"] = list(
                parsed.comparison_config_version_ids
            )
        native_members = {row["run_id"]: row for row in native["members"]}
        for member in body["members"]:
            member.update(native_members.get(member["run_id"], {}))
        body["metrics"] = DBExecutionAnalysisRepository._metrics(
            value["facts"], parsed, allow_automatic_comparison=allow_automatic
        )
        body["metrics"]["evaluation_series"] = point_series(points["records"])
        body["metrics"]["accounting_coverage"] = value["facts"]["accounting_coverage"]
        # Public limits describe durable comparison semantics, not the reused A01 envelope TTL.
        body["metrics"]["limits"] = {
            "primary_members": 100000,
            "accounting_members": 1000000,
            "detail_runs": 5,
            "retention": "durable_revision",
        }
        members = body["members"]
        body["next_cursor"] = (
            self._cursor(scope, principal, comparison_id, revision, members[limit - 1]["ordinal"])
            if len(members) > limit
            else None
        )
        body["members"] = [
            {k: v for k, v in member.items() if k != "ordinal"} for member in members[:limit]
        ]
        for member in body["members"]:
            raw = json.dumps(
                {
                    "comparison_id": comparison_id,
                    "revision": revision,
                    "run_id": member["run_id"],
                    "cut": member["cut"],
                },
                sort_keys=True,
            ).encode()
            member["cut"] = hmac.new(
                self.secret.encode(), b"comparison-cut:" + raw, hashlib.sha256
            ).hexdigest()
        body.pop("revision_id", None)
        return ComparisonRead(
            body,
            AuthorityState(
                value["authority_revision"], value["manifest"] + ":" + points["fingerprint"]
            ),
        )

    async def current(self, scope, principal, comparison_id, revision):
        async with self.transactions.transaction(scope, principal, current=True) as db:
            value = await self._operation(
                db,
                scope,
                principal,
                "control",
                "current",
                comparison_id=comparison_id,
                revision=revision,
            )
            signed = await DBCurrentAuthority(db, signing_secret=self.secret).signed(
                scope,
                principal,
                operation="resolve",
                comparison_id=comparison_id,
                revision=revision,
            )
            capture_id = await db.scalar(
                text("SELECT public.opencitadel_comparison_native_resolve(:body,:signature)"),
                signed,
            )
            points = await points_operation(
                db,
                scope,
                principal,
                secret=self.secret,
                kind="comparison",
                capture=capture_id,
                operation="current",
            )
        return AuthorityState(
            value["authority_revision"], value["manifest"] + ":" + points["fingerprint"]
        )

    async def mutate(self, scope, principal, function, operation, **payload):
        for attempt in range(3):
            try:
                async with self.transactions.transaction(scope, principal) as db:
                    return await self._operation(
                        db, scope, principal, function, operation, **payload
                    )
            except DBAPIError as exc:
                code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                if code not in {"40001", "40P01"} or attempt == 2:
                    raise
        raise RuntimeError("comparison_retry_exhausted")

    async def align(
        self, scope, principal, comparison_id, revision, *, expected_revision, edits, request_id
    ):
        value = await self.mutate(
            scope,
            principal,
            "control",
            "align",
            comparison_id=comparison_id,
            revision=revision,
            expected_revision=expected_revision,
            edits=edits,
            request_id=request_id,
        )
        return value["alignment_revision"]

    async def artifact_context(self, scope, principal, comparison_id, revision, selection):
        async with self.transactions.transaction(scope, principal, current=True) as db:
            return await self._operation(
                db,
                scope,
                principal,
                "control",
                "artifact",
                comparison_id=comparison_id,
                revision=revision,
                selection=selection,
            )

    async def content_snapshot(
        self, scope, principal, comparison_id, revision, run_id, step_id, kind
    ):
        from app.infrastructure.repositories.db_execution_content_repository import (
            DBExecutionContentRepository,
        )

        async with self.transactions.transaction(scope, principal, current=True) as db:
            signed = await DBCurrentAuthority(db, signing_secret=self.secret).signed(
                scope,
                principal,
                operation="content_context",
                comparison_id=comparison_id,
                revision=revision,
                run_id=run_id,
                step_id=step_id,
                kind=kind,
            )
            from app.infrastructure.repositories.db_analysis_native import native_scalar

            context = await native_scalar(
                db, "SELECT public.opencitadel_comparison_content_context(:body,:signature)", signed
            )
            if context is None:
                return None
            return await DBExecutionContentRepository(db).get_snapshot(
                scope, context["content_id"], run_id, step_id, context["formal_position"]
            )
