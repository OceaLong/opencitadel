#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import io
import json
from datetime import datetime, timezone
from typing import AsyncGenerator, Callable, Optional, Any, Dict, List

from sqlalchemy import desc, func, select

from app.domain.models.audit_log import AuditLog
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.audit_chain import GENESIS, compute_entry_hash, entry_fields
from app.infrastructure.models.audit_log import AuditLogORM
from core.config import get_settings


class AuditService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def record(self, log: AuditLog) -> None:
        async with self._uow_factory() as uow:
            await uow.audit.add(log)

    async def list_logs(
            self,
            *,
            actor_user_id: Optional[str] = None,
            action: Optional[str] = None,
            resource_id: Optional[str] = None,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
            limit: int = 100,
            offset: int = 0,
    ) -> list[AuditLog]:
        async with self._uow_factory() as uow:
            return await uow.audit.list(
                actor_user_id=actor_user_id,
                action=action,
                resource_id=resource_id,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
                offset=offset,
            )

    async def build_session_audit_report(self, session_id: str) -> str:
        payload = await self.build_session_audit_report_json(session_id)
        lines = [
            f"# 会话审计报告\n\nSession: `{session_id}`\n\n",
            "## 治理动作\n\n",
        ]
        governance = payload.get("governance_actions") or []
        if not governance:
            lines.append("_无治理动作记录_\n\n")
        else:
            for item in governance:
                lines.append(
                    f"- **{item.get('created_at')}** `{item.get('action')}` "
                    f"actor={item.get('actor_user_id') or 'system'} metadata={item.get('metadata')}\n"
                )
            lines.append("\n")
        lines.append("## 工具调用明细\n\n")
        tools = payload.get("tool_invocations") or []
        if not tools:
            lines.append("_无工具调用记录_\n\n")
        else:
            for item in tools:
                lines.append(
                    f"- **{item.get('created_at')}** `{item.get('tool')}` "
                    f"success={item.get('success')} duration_ms={item.get('duration_ms')} "
                    f"args={item.get('args')} result={item.get('result_summary')}\n"
                )
        return "".join(lines)

    async def build_session_audit_report_json(self, session_id: str) -> Dict[str, Any]:
        logs = await self.list_logs(resource_id=session_id, limit=1000)
        governance: List[Dict[str, Any]] = []
        tool_invocations: List[Dict[str, Any]] = []
        for log in logs:
            entry = {
                "id": log.id,
                "created_at": log.created_at.isoformat(),
                "action": log.action,
                "actor_user_id": log.actor_user_id,
                "metadata": log.metadata,
            }
            if log.action == "agent_tool_invoke":
                meta = log.metadata or {}
                tool_invocations.append({
                    **entry,
                    "tool": meta.get("tool"),
                    "args": meta.get("args"),
                    "success": meta.get("success"),
                    "result_summary": meta.get("result_summary"),
                    "duration_ms": meta.get("duration_ms"),
                    "gate_profile": meta.get("gate_profile"),
                    "gated": meta.get("gated"),
                })
            else:
                governance.append(entry)
        return {
            "session_id": session_id,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "governance_actions": governance,
            "tool_invocations": tool_invocations,
        }

    async def build_session_audit_report_json_text(self, session_id: str) -> str:
        return json.dumps(
            await self.build_session_audit_report_json(session_id),
            ensure_ascii=False,
            indent=2,
        )

    async def verify_chain(self, *, limit: Optional[int] = None) -> dict:
        secret = get_settings().api_key_secret
        async with self._uow_factory() as uow:
            logs = await uow.audit.list_chained(limit=limit)
        return self._verify_logs(logs, secret)

    async def verify_session_chain(self, session_id: str) -> dict:
        global_result = await self.verify_chain()
        async with self._uow_factory() as uow:
            session_logs = await uow.audit.list_chained(resource_id=session_id)
        if not session_logs:
            return {
                **global_result,
                "session_id": session_id,
                "session_entries": 0,
                "session_ok": global_result.get("ok", False),
            }
        secret = get_settings().api_key_secret
        session_verify = self._verify_logs(session_logs, secret)
        return {
            **global_result,
            "session_id": session_id,
            "session_entries": len(session_logs),
            "session_ok": session_verify.get("ok", False),
            "session_first_broken_seq": session_verify.get("first_broken_seq"),
        }

    @staticmethod
    def _verify_logs(logs: list[AuditLog], secret: str) -> dict:
        from datetime import datetime, timezone

        prev_hash = GENESIS
        first_broken: Optional[int] = None
        for log in logs:
            if log.chain_seq is None or not log.entry_hash:
                first_broken = log.chain_seq
                break
            if log.prev_hash != prev_hash:
                first_broken = log.chain_seq
                break
            fields = entry_fields(
                chain_seq=log.chain_seq,
                id=log.id,
                actor_user_id=log.actor_user_id,
                actor_ip=log.actor_ip,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                team_id=log.team_id,
                request_id=log.request_id,
                metadata=log.metadata,
                created_at=log.created_at,
            )
            expected = compute_entry_hash(secret, fields, prev_hash)
            if expected != log.entry_hash:
                first_broken = log.chain_seq
                break
            prev_hash = log.entry_hash
        return {
            "ok": first_broken is None,
            "total": len(logs),
            "first_broken_seq": first_broken,
            "checked_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }

    async def summarize(
            self,
            *,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
    ) -> dict:
        async with self._uow_factory() as uow:
            day_bucket = func.date(AuditLogORM.created_at).label("date")
            day_stmt = select(day_bucket, func.count(AuditLogORM.id)).group_by(day_bucket).order_by(day_bucket)
            action_stmt = (
                select(AuditLogORM.action, func.count(AuditLogORM.id))
                .group_by(AuditLogORM.action)
                .order_by(desc(func.count(AuditLogORM.id)))
            )
            if start_at:
                day_stmt = day_stmt.where(AuditLogORM.created_at >= start_at)
                action_stmt = action_stmt.where(AuditLogORM.created_at >= start_at)
            if end_at:
                day_stmt = day_stmt.where(AuditLogORM.created_at <= end_at)
                action_stmt = action_stmt.where(AuditLogORM.created_at <= end_at)

            day_result = await uow.db_session.execute(day_stmt)  # type: ignore[attr-defined]
            action_result = await uow.db_session.execute(action_stmt)  # type: ignore[attr-defined]

        return {
            "by_day": [
                {"date": str(day), "count": int(count or 0)}
                for day, count in day_result.all()
            ],
            "by_action": [
                {"action": action, "count": int(count or 0)}
                for action, count in action_result.all()
            ],
        }

    async def export_csv(self) -> AsyncGenerator[str, None]:
        yield "id,actor_user_id,action,resource_type,resource_id,team_id,request_id,created_at\n"
        offset = 0
        while True:
            logs = await self.list_logs(limit=500, offset=offset)
            if not logs:
                break
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for log in logs:
                writer.writerow([
                    log.id,
                    log.actor_user_id or "",
                    log.action,
                    log.resource_type,
                    log.resource_id,
                    log.team_id or "",
                    log.request_id,
                    log.created_at.isoformat(),
                ])
            yield buffer.getvalue()
            offset += len(logs)
