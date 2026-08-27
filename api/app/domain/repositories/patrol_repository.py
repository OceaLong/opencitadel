"""Persistence port for Ops Patrol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolFinding,
    PatrolPack,
    PatrolRemediation,
    PatrolRun,
)
from app.domain.models.scope import OwnerScope


class PatrolRepository(Protocol):
    async def save_pack(self, pack: PatrolPack) -> PatrolPack: ...
    async def get_pack(
        self, pack_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolPack | None: ...
    async def list_packs(
        self, scope: OwnerScope, *, limit: int = 20, offset: int = 0
    ) -> list[PatrolPack]: ...
    async def save_run(
        self,
        run: PatrolRun,
        *,
        parent_run_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> PatrolRun: ...
    async def get_run(
        self, run_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolRun | None: ...
    async def get_run_by_session_id(self, session_id: str) -> PatrolRun | None: ...
    async def get_run_by_idempotency_key(self, key: str) -> PatrolRun | None: ...
    async def get_active_run_for_pack(self, pack_id: str) -> PatrolRun | None: ...
    async def list_runs(
        self,
        scope: OwnerScope,
        *,
        pack_id: str | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[PatrolRun]: ...
    async def save_check_results(
        self, items: list[PatrolCheckResult]
    ) -> list[PatrolCheckResult]: ...
    async def list_check_results(
        self, run_id: str, scope: OwnerScope | None = None
    ) -> list[PatrolCheckResult]: ...
    async def save_finding(self, finding: PatrolFinding) -> PatrolFinding: ...
    async def get_finding(
        self, finding_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolFinding | None: ...
    async def list_findings(
        self, run_id: str, scope: OwnerScope | None = None
    ) -> list[PatrolFinding]: ...
    async def get_open_finding_by_fingerprint(self, fingerprint: str) -> PatrolFinding | None: ...
    async def save_remediation(self, remediation: PatrolRemediation) -> PatrolRemediation: ...
    async def get_remediation(
        self, remediation_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolRemediation | None: ...
    async def list_remediations_for_run(
        self, run_id: str, scope: OwnerScope | None = None
    ) -> list[PatrolRemediation]: ...
    async def get_active_remediation_for_finding(
        self, finding_id: str
    ) -> PatrolRemediation | None: ...
    async def get_remediation_by_session_id(self, session_id: str) -> PatrolRemediation | None: ...
    async def get_remediation_by_recheck_run_id(self, run_id: str) -> PatrolRemediation | None: ...
    async def daily_run_finding_counts(self, since: datetime) -> list[dict]:
        """Per-day run and (newly first-seen) finding counts since ``since``.

        Returns one row per date that has at least one run or finding, e.g.
        ``[{"date": "2026-08-01", "runs": 3, "findings": 1}]``, ascending by
        date. Dates with only runs or only findings still appear with the
        other counter at 0 -- gaps for dates with neither are left for the
        caller to fill.
        """
        ...

    async def remediation_status_counts(self, since: datetime) -> dict[str, int]:
        """Count of ``PatrolRemediation`` rows created since ``since``, grouped
        by ``status``. Statuses with zero rows in the window are omitted."""
        ...
