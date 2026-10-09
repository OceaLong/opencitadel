"""User commands persist bounded intent; only the kernel scheduler creates Runs."""

import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

from app.domain.evaluation.batch import BatchView, CaseResult, CaseSlot, manual_retry_allowed
from app.domain.models.authorization import AuthorizationContext


class BatchService:
    def __init__(self, suites, *, preflight_factory):
        self.suites = suites
        self.preflight_factory = preflight_factory

    async def start(self, scope, principal, request_id, payload):
        prior = await self._existing(scope, principal, "start", request_id, payload)
        if prior is not None:
            return prior
        suite_id = UUID(str(payload["suite_version"]))
        # Immutable bytes are verified without holding scheduler rows. No members/Run expansion.
        suite = await self.suites.get_version(scope, principal, "suite", suite_id)
        await self.suites.datasets.get_version(scope, principal, suite.dataset_version)
        pair = await self.suites.policies.load_active_pair()
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            saved = await work.evaluation_batch.preflight(
                scope, suite_id, payload["preflight_revision"]
            )
            if not saved or not saved["allowed"]:
                raise ValueError("preflight_required")
            check = await self.preflight_factory(principal).revalidate_for_start(
                scope, suite_id, uow=work, policy_pair=pair
            )
            if not check.allowed:
                raise ValueError("batch_preflight_rejected")
            repo = work.evaluation_batch
            identity = await repo.submit(scope, principal, "start", request_id, payload, uuid4())
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            result = await self._view(repo, scope, identity)
            await self._audit(work, scope, principal, request_id, "start", identity)
            await work.commit()
            return result

    async def cancel(self, scope, principal, request_id, payload):
        identity = UUID(str(payload["batch_id"]))
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            repo = work.evaluation_batch
            await repo.submit(scope, principal, "cancel", request_id, payload, identity)
            result = await self._view(repo, scope, identity)
            await self._audit(work, scope, principal, request_id, "cancel", identity)
            await work.commit()
            return result

    async def retry_failed(self, scope, principal, request_id, payload):
        prior = await self._existing(scope, principal, "retry_failed", request_id, payload)
        if prior is not None:
            return prior
        parent_id = UUID(str(payload["batch_id"]))
        pair = await self.suites.policies.load_active_pair()
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            repo = work.evaluation_batch
            parent = await repo.get(scope, parent_id)
            check = await self.preflight_factory(principal).revalidate_for_start(
                scope, parent["suite_version"], uow=work, policy_pair=pair
            )
            rows = await repo.results(scope, parent_id, limit=5000)
            selected = [
                str(row["id"])
                for row in rows
                if not row["recovery_pending"]
                and manual_retry_allowed(
                    row["execution_status"],
                    row["scoring_status"],
                    resources_available=check.allowed,
                    unknown=row["unknown_effect"]
                    or await repo.unknown_effect(
                        scope, row["run_id"], include_unresolved=True, principal=principal
                    ),
                )
            ]
            if not selected:
                raise ValueError("no_retryable_results")
            request = {
                "suite_version": str(parent["suite_version"]),
                "parent_batch": str(parent_id),
                "selected_slots": selected,
                "request_payload": payload,
            }
            identity = await repo.submit(
                scope, principal, "retry_failed", request_id, request, uuid4()
            )
            result = await self._view(repo, scope, identity)
            await self._audit(work, scope, principal, request_id, "retry_failed", identity)
            await work.commit()
            return result

    async def _existing(self, scope, principal, kind, request_id, payload):
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            repo = work.evaluation_batch
            identity = await repo.command(scope, kind, request_id, payload)
            return await self._view(repo, scope, identity) if identity is not None else None

    async def get(self, scope, principal, batch_id):
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            return await self._view(work.evaluation_batch, scope, batch_id)

    @staticmethod
    async def _view(repo, scope, batch_id):
        row = await repo.get(scope, batch_id)
        counts = await repo.counts(scope, batch_id)
        return BatchView(
            id=row["id"],
            revision=row["revision"],
            status=row["status"],
            review_status=row["review_status"],
            cleanup_status=row["cleanup_status"],
            counts=counts,
        )

    async def results(self, scope, principal, batch_id, *, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = [scope.model_dump(mode="json"), principal.user_id, str(batch_id)]
        after = -1
        secret = self.suites.cursor_secret
        if cursor:
            try:
                raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                body, mac = raw[:-32], raw[-32:]
                decoded = json.loads(body)
                if (
                    len(cursor) > 4096
                    or not hmac.compare_digest(mac, hmac.new(secret, body, hashlib.sha256).digest())
                    or decoded["context"] != context
                ):
                    raise ValueError()
                after = int(decoded["after"])
            except (ValueError, TypeError, KeyError):
                raise ValueError("invalid_cursor") from None
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            repo = work.evaluation_batch
            await repo.get(scope, batch_id)
            rows = await repo.results(scope, batch_id, after=after, limit=limit + 1)
            next_cursor = None
            if len(rows) > limit:
                body = json.dumps(
                    {"context": context, "after": rows[limit - 1]["ordinal"]}, separators=(",", ":")
                ).encode()
                next_cursor = (
                    base64.urlsafe_b64encode(body + hmac.new(secret, body, hashlib.sha256).digest())
                    .decode()
                    .rstrip("=")
                )
            return {
                "items": [
                    CaseResult(
                        id=row["id"],
                        slot=CaseSlot(
                            case_revision_id=row["case_revision_id"],
                            config_version_id=row["config_version_id"],
                            repetition=row["repetition"],
                        ),
                        run_id=row["run_id"],
                        execution_status=row["execution_status"],
                        scoring_status=row["scoring_status"],
                        attempt=row["attempt"],
                        result_revision=row["revision"],
                    )
                    for row in rows[:limit]
                ],
                "next_cursor": next_cursor,
            }

    async def environments(self, scope, principal, batch_id, *, cursor=None, limit=100):
        from app.domain.evaluation.batch import BatchEnvironment
        from app.domain.evaluation.environment import reusable

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = [
            "batch-environments-v1",
            scope.model_dump(mode="json"),
            principal.user_id,
            str(batch_id),
            limit,
        ]
        after = None
        secret = self.suites.cursor_secret
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                body, mac = raw[:-32], raw[-32:]
                decoded = json.loads(body)
                if (
                    not hmac.compare_digest(mac, hmac.new(secret, body, hashlib.sha256).digest())
                    or decoded["context"] != context
                ):
                    raise ValueError()
                after = UUID(decoded["after"])
            except (ValueError, TypeError, KeyError):
                raise ValueError("invalid_cursor") from None
        async with self.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            repo = work.evaluation_batch
            await repo.get(scope, batch_id)
            rows = await repo.environment_statuses(scope, batch_id, after=after, limit=limit + 1)
            next_cursor = None
            if len(rows) > limit:
                body = json.dumps(
                    {"context": context, "after": str(rows[limit - 1]["id"])}, separators=(",", ":")
                ).encode()
                next_cursor = (
                    base64.urlsafe_b64encode(body + hmac.new(secret, body, hashlib.sha256).digest())
                    .decode()
                    .rstrip("=")
                )
            fields = set(BatchEnvironment.model_fields) - {"reusable"}
            return {
                "items": [
                    BatchEnvironment(
                        **{key: row[key] for key in fields}, reusable=reusable(row["state"])
                    )
                    for row in rows[:limit]
                ],
                "next_cursor": next_cursor,
            }

    @staticmethod
    async def _audit(work, scope, principal, request_id, kind, batch_id):
        from app.domain.models.audit_log import AuditLog

        command_id = work.evaluation_batch.new_command
        if command_id is not None:
            await work.audit.add_batch(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.batch." + kind,
                    resource_type="evaluation_batch",
                    resource_id=str(batch_id),
                    request_id=request_id,
                    metadata={"command_id": str(command_id)},
                ),
                authorization=AuthorizationContext.for_principal(
                    principal, scope=scope, request_id=request_id
                ),
            )
