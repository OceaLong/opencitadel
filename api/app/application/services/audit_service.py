import csv
import io
import json
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any

from app.application.ports.observability import GovernanceMetricsPort
from app.application.ports.queries import AuditSummaryQueryPort
from app.application.ports.reporting import AuditVerificationKeyring
from app.application.security.authorization_context import authorization_scope
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.audit_chain import verify_chain_logs

_AUDIT_SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "credential",
    "cookie",
    "access_token",
    "refresh_token",
    "headers",
)
_AUDIT_REDACTED = "[REDACTED]"
logger = logging.getLogger(__name__)


def sanitize_audit_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(hint in normalized for hint in _AUDIT_SECRET_KEY_HINTS):
                sanitized[str(key)] = _AUDIT_REDACTED
            else:
                sanitized[str(key)] = sanitize_audit_metadata(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_metadata(item) for item in value]
    return value


class AuditService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        verification_keyring: AuditVerificationKeyring,
        governance_metrics: GovernanceMetricsPort,
        summary_query: AuditSummaryQueryPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._verification_keyring = verification_keyring
        self._governance_metrics = governance_metrics
        self._summary_query = summary_query

    async def record(self, log: AuditLog) -> None:
        sanitized = log.model_copy(update={"metadata": sanitize_audit_metadata(log.metadata)})
        # Appending to the tamper-evident audit chain must read the current chain
        # tip (written by any actor) and insert the new head. audit_logs is
        # system/admin-read and append-only, so recording runs under a trusted
        # system scope; the actor identity is preserved in the row itself.
        with authorization_scope(AuthorizationContext.system("audit")):
            async with self._uow_factory() as uow:
                await uow.audit.add(sanitized)
                await uow.commit()

    async def list_logs(
        self,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        async with self._uow_factory() as uow:
            return await uow.audit.list(
                actor_user_id=actor_user_id,
                action=action,
                resource_id=resource_id,
                resource_type=resource_type,
                session_id=session_id,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
                offset=offset,
            )

    async def get_log(self, log_id: str) -> AuditLog | None:
        async with self._uow_factory() as uow:
            return await uow.audit.get_by_id(log_id)

    async def build_session_audit_report(self, session_id: str) -> str:
        payload = await self.build_session_audit_report_json(session_id)
        lines = [
            f"# 会话审计报告\n\nSession: `{session_id}`\n\n",
            "## 审计条目\n\n",
        ]
        entries = payload.get("entries") or []
        if not entries:
            lines.append("_无审计条目_\n\n")
        else:
            lines.extend(
                (
                    f"- **{item.get('created_at')}** `{item.get('action')}` "
                    f"actor={item.get('actor_user_id') or 'system'} metadata={item.get('metadata')}\n"
                )
                for item in entries
            )
        return "".join(lines)

    async def build_session_audit_report_json(self, session_id: str) -> dict[str, Any]:
        logs = await self.list_logs(session_id=session_id, limit=1000)
        entries = [
            {
                "id": log.id,
                "created_at": log.created_at.isoformat(),
                "action": log.action,
                "actor_user_id": log.actor_user_id,
                "metadata": log.metadata,
            }
            for log in logs
        ]
        return {
            "session_id": session_id,
            "generated_at": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "entries": entries,
        }

    async def build_session_audit_report_json_text(self, session_id: str) -> str:
        return json.dumps(
            await self.build_session_audit_report_json(session_id),
            ensure_ascii=False,
            indent=2,
        )

    async def verify_chain(self, *, limit: int | None = None) -> dict:
        async with self._uow_factory() as uow:
            logs = await uow.audit.list_chained(limit=limit)
        result = self._verify_logs(logs, dict(self._verification_keyring.keys))
        self._governance_metrics.record_chain_verification("intact" if result["ok"] else "broken")
        if not result["ok"]:
            logger.critical(
                "AUDIT_CHAIN_INTEGRITY_FAILURE first_broken_seq=%s total=%s",
                result["first_broken_seq"],
                result["total"],
            )
        return result

    async def verify_session_chain(self, session_id: str) -> dict:
        global_result = await self.verify_chain()
        async with self._uow_factory() as uow:
            session_logs = await uow.audit.list_chained(session_id=session_id)
        if not session_logs:
            return {
                **global_result,
                "session_id": session_id,
                "session_entries": 0,
                "session_ok": global_result.get("ok", False),
            }
        # A session's rows span shards and are only a filtered slice of each,
        # so they are not a standalone chain: a session-first entry normally
        # does not follow its shard's GENESIS and unrelated entries sit between
        # session entries. Session integrity therefore inherits the full
        # (per-shard) chain verification result rather than re-walking a subset.
        return {
            **global_result,
            "session_id": session_id,
            "session_entries": len(session_logs),
            "session_ok": global_result.get("ok", False),
            "session_first_broken_seq": global_result.get("first_broken_seq"),
        }

    @staticmethod
    def _verify_logs(
        logs: list[AuditLog],
        keys: dict[str, tuple[str, ...]],
    ) -> dict:
        # The chain is now sharded (per team / per user / system); each shard is
        # an independent prev_hash chain. verify_chain_logs walks each shard on
        # its own and returns a single aggregate {ok, total, first_broken_seq}.
        return verify_chain_logs(logs, keys)

    async def summarize(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict:
        summary = await self._summary_query.summarize(start_at=start_at, end_at=end_at)
        return {
            "by_day": [{"date": point.key, "count": point.count} for point in summary.by_day],
            "by_action": [
                {"action": point.key, "count": point.count} for point in summary.by_action
            ],
        }

    async def export_csv(
        self,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> AsyncGenerator[str, None]:
        yield (
            "id,actor_user_id,action,resource_type,resource_id,team_id,"
            "session_id,request_id,created_at\n"
        )
        offset = 0
        while True:
            logs = await self.list_logs(
                actor_user_id=actor_user_id,
                action=action,
                resource_id=resource_id,
                resource_type=resource_type,
                session_id=session_id,
                start_at=start_at,
                end_at=end_at,
                limit=500,
                offset=offset,
            )
            if not logs:
                break
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for log in logs:
                writer.writerow(
                    [
                        log.id,
                        log.actor_user_id or "",
                        log.action,
                        log.resource_type,
                        log.resource_id,
                        log.team_id or "",
                        log.session_id or "",
                        log.request_id,
                        log.created_at.isoformat(),
                    ]
                )
            yield buffer.getvalue()
            offset += len(logs)
