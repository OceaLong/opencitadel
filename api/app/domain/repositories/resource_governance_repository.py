#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional, Protocol

from app.domain.models.tool_approval import (
    ApprovalStatus,
    ToolApprovalBatch,
    ToolApprovalCall,
)
from app.domain.models.resource_governance import (
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
    SessionResourceBinding,
)


class ResourceGovernanceRepository(Protocol):
    async def add_build(self, build: ResourceBuild) -> ResourceBuild:
        """Insert one resource build."""
        ...

    async def get_build(
        self,
        build_id: str,
        *,
        for_update: bool = False,
    ) -> Optional[ResourceBuild]:
        """Return a build, optionally locking it for event allocation."""
        ...

    async def get_active_build(
        self,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> Optional[ResourceBuild]:
        """Return the database-authoritative queued/running resource build."""
        ...

    async def list_stale_builds(
        self,
        resource_kind: ResourceKind,
        *,
        stale_before: datetime,
        limit: int = 100,
    ) -> list[ResourceBuild]:
        """Return stale queued/running builds from the durable authority."""
        ...

    async def append_event(
        self,
        build_id: str,
        event: ResourceBuildEvent,
    ) -> int:
        """Append an event and atomically advance the locked build cursor."""
        ...

    async def get_event(
        self,
        build_id: str,
        seq: int,
    ) -> Optional[ResourceBuildEvent]:
        """Return one committed build event by its per-build sequence."""
        ...

    async def list_events(
        self,
        build_id: str,
        after_seq: int,
        limit: int,
    ) -> list[ResourceBuildEvent]:
        """List events strictly after a cursor in ascending sequence order."""
        ...

    async def add_binding(
        self,
        binding: SessionResourceBinding,
    ) -> SessionResourceBinding:
        """Insert one immutable binding history row."""
        ...

    async def get_current_binding(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        *,
        for_update: bool = False,
    ) -> Optional[SessionResourceBinding]:
        """Return the single current row, optionally with a row lock."""
        ...

    async def list_current_bindings(
        self,
        session_id: str,
    ) -> list[SessionResourceBinding]:
        """Return current bindings in stable resource-kind order."""
        ...

    async def list_bindings(
        self,
        session_id: str,
        resource_kind: Optional[ResourceKind] = None,
    ) -> list[SessionResourceBinding]:
        """Return immutable binding history in creation order."""
        ...

    async def replace_current_binding(
        self,
        current: SessionResourceBinding,
        replacement: SessionResourceBinding,
    ) -> SessionResourceBinding:
        """Deactivate and supersede a locked current row in this transaction."""
        ...

    async def save_approval_batch(self, batch: ToolApprovalBatch) -> None:
        """Persist the complete preflighted batch and every call."""
        ...

    async def get_pending_approval_batch(
        self, session_id: str
    ) -> Optional[ToolApprovalBatch]:
        """Return the newest unexpired pending batch for a session."""
        ...

    async def get_approval_batch(
        self, batch_id: str
    ) -> Optional[ToolApprovalBatch]:
        """Return a batch by ID without claiming or mutating it."""
        ...

    async def decide_approval_call(
        self,
        tool_call_id: str,
        status: ApprovalStatus,
        decided_by: str,
    ) -> Optional[ToolApprovalCall]:
        """Apply one immutable, idempotent approval decision."""
        ...

    async def consume_approval_batch(
        self, batch_id: str
    ) -> Optional[ToolApprovalBatch]:
        """Atomically consume an approved batch.

        Only the winning transition returns ``execution_claimed=True``;
        repeated calls return the persisted consumed batch without a claim.
        """
        ...
