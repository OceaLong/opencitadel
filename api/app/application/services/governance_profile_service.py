#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Per-session governance profile read-model.

Aggregates data already recorded by the governance execution chain
(audit log hash chain, checkpoints, session state) into a single
auditor-facing JSON document. Read-only: no new tables, no new writes.
"""
from typing import Any, Callable, Dict, Optional

from app.application.errors.exceptions import NotFoundError
from app.application.services.audit_service import AuditService, sanitize_audit_metadata
from app.domain.models.audit_log import AuditLog
from app.domain.models.checkpoint import Checkpoint
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.checkpoint_service import CheckpointService

_APPROVAL_ACTIONS = frozenset({
    "agent_tool_approve",
    "agent_tool_reject",
    "agent_plan_approve",
    "agent_plan_reject",
    "agent_takeover",
    "agent_takeover_skip",
    "agent_takeover_timeout",
    "agent_rollback",
})


class GovernanceProfileService:
    """Builds the per-session governance profile consumed by the auditor API."""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            audit_service: AuditService,
            checkpoint_service: CheckpointService,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._checkpoint_service = checkpoint_service

    async def build_profile(self, session_id: str, scope: Optional[OwnerScope]) -> Dict[str, Any]:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=scope)
            if session is None:
                # Cross-tenant sessions must not be distinguishable from
                # missing ones, so this branch covers both "does not
                # exist" and "exists but out of scope".
                raise NotFoundError("session not found")
            logs = await uow.audit.list_chained(resource_id=session_id)

        checkpoints = await self._checkpoint_service.list_checkpoints(session_id)
        chain = await self._audit_service.verify_session_chain(session_id)

        ordered_logs = sorted(logs, key=lambda log: log.created_at)
        approvals = [
            self._approval_row(log) for log in ordered_logs
            if log.action in _APPROVAL_ACTIONS
        ]
        gate_hits = [
            self._gate_row(log) for log in ordered_logs
            if log.action == "agent_tool_invoke" and (log.metadata or {}).get("gated")
        ]
        denials = [
            self._denial_row(log) for log in ordered_logs
            if log.action == "agent_tool_denied"
        ]

        return {
            "session": self._session_row(session),
            "chain": {
                "verified": bool(chain.get("session_ok", chain.get("ok"))),
                "checked_entries": chain.get("session_entries", chain.get("total")),
            },
            "approvals": approvals,
            "gate_hits": gate_hits,
            "checkpoints": [self._checkpoint_row(cp) for cp in checkpoints],
            "terminal": {
                "status": session.status.value,
                "reached_at": session.updated_at.isoformat(),
            },
            "denials": denials,
        }

    @staticmethod
    def _session_row(session: Session) -> Dict[str, Any]:
        return {
            "id": session.id,
            "title": session.title,
            "status": session.status.value,
            "gate_profile": session.gate_profile,
            "operator_scope": session.operator_scope,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }

    @staticmethod
    def _approval_row(log: AuditLog) -> Dict[str, Any]:
        meta = sanitize_audit_metadata(log.metadata or {})
        return {
            "action": log.action,
            "decision": meta.get("decision"),
            "actor_user_id": log.actor_user_id,
            "created_at": log.created_at.isoformat(),
            "pending_phase": meta.get("pending_phase"),
            "tool": meta.get("tool"),
            "approval_batch_id": meta.get("approval_batch_id"),
            "feedback": meta.get("feedback"),
        }

    @staticmethod
    def _gate_row(log: AuditLog) -> Dict[str, Any]:
        meta = sanitize_audit_metadata(log.metadata or {})
        return {
            "tool": meta.get("tool"),
            "gated": meta.get("gated"),
            "gate_profile": meta.get("gate_profile"),
            "created_at": log.created_at.isoformat(),
        }

    @staticmethod
    def _denial_row(log: AuditLog) -> Dict[str, Any]:
        meta = sanitize_audit_metadata(log.metadata or {})
        return {
            "tool": meta.get("tool"),
            "layer": meta.get("layer"),
            "reason": meta.get("reason"),
            "created_at": log.created_at.isoformat(),
        }

    @staticmethod
    def _checkpoint_row(checkpoint: Checkpoint) -> Dict[str, Any]:
        return {
            "id": checkpoint.id,
            "anchor_type": checkpoint.anchor_type.value,
            "label": checkpoint.label,
            "created_at": checkpoint.created_at.isoformat(),
        }
