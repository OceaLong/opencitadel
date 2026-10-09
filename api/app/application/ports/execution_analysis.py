"""Internal analysis capture contract; authority revisions never enter public envelopes."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from app.domain.analysis.metrics import METRIC_VERSION, resolve_timezone


class CoverageChanged(PermissionError):
    def __init__(self):
        super().__init__("analysis_coverage_changed")


@dataclass(frozen=True)
class AnalysisQuery:
    start: datetime
    end: datetime
    grain: str
    timezone: str
    filters: tuple[tuple[str, str], ...]
    comparison_config_version_ids: tuple[str, ...] = ()
    start_explicit: bool = False
    end_explicit: bool = False

    @classmethod
    def parse(cls, filters, grain, timezone, *, workspace_timezone=None, now=None):
        now = now or datetime.now(UTC)
        allowed = {
            "family",
            "session",
            "model_revision",
            "configuration_revision",
            "mode",
            "tool",
            "status",
            "purpose",
            "batch_id",
            "accounting",
            "comparison_config_version_ids",
            "start",
            "end",
        }
        if set(filters) - allowed or grain not in {"hour", "day"}:
            raise ValueError("invalid_analysis_query")

        def instant(value, fallback):
            result = datetime.fromisoformat(value) if isinstance(value, str) else value or fallback
            if not isinstance(result, datetime) or result.tzinfo is None:
                raise ValueError("aware_analysis_time_required")
            return result.astimezone(UTC)

        end = instant(filters.get("end"), now)
        start = instant(filters.get("start"), end - timedelta(days=7))
        if not timedelta(0) < end - start <= timedelta(days=90):
            raise ValueError("analysis_range_exceeded")
        if filters.get("accounting", "run") not in {"run", "selected_result", "batch_total"} or (
            filters.get("accounting") == "batch_total" and not filters.get("batch_id")
        ):
            raise ValueError("invalid_accounting_grain")
        selection = filters.get("comparison_config_version_ids", [])
        if not isinstance(selection, (list, tuple)) or len(selection) > 5:
            raise ValueError("invalid_comparison_selection")
        selection = tuple(str(UUID(value)) for value in selection)
        if len(set(selection)) != len(selection):
            raise ValueError("invalid_comparison_selection")
        normalized = []
        for name, value in filters.items():
            if name in {"start", "end", "comparison_config_version_ids"}:
                continue
            if not isinstance(value, str) or not 1 <= len(value) <= 255:
                raise ValueError("invalid_analysis_filter")
            normalized.append((name, value))
        return cls(
            start,
            end,
            grain,
            resolve_timezone(workspace_timezone, timezone),
            tuple(sorted(normalized)),
            tuple(sorted(selection)),
            "start" in filters,
            "end" in filters,
        )


@dataclass(frozen=True)
class AuthorityState:
    revision: int
    manifest: str


@dataclass(frozen=True)
class AnalysisCapture:
    watermark: str
    authority: AuthorityState
    metrics: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricEnvelope:
    metrics: dict[str, Any]
    grain: str
    timezone: str
    watermark: str
    metric_version: str = METRIC_VERSION


class ExecutionAnalysisPort(Protocol):
    async def capture(
        self, scope, principal, query: AnalysisQuery, watermark: str | None
    ) -> AnalysisCapture: ...
    async def current(self, scope, principal, capture: AnalysisCapture) -> AuthorityState:
        """A new READ COMMITTED primary transaction, never the capture snapshot."""
        ...


def same_query(saved, requested):
    requested = dict(requested)
    for name in ("start", "end"):
        if not requested.get(name + "_explicit", True):
            requested[name] = saved[name]
    return saved == requested
