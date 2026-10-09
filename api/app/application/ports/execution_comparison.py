"""Durable comparison capture and fresh authority boundaries."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.application.ports.execution_analysis import AnalysisQuery, AuthorityState
from app.domain.analysis.comparison import validate_detail_runs


def command_request_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or value.strip() != value:
        raise ValueError("invalid_comparison_request_id")
    return value


def intent_fingerprint(payload):
    intent = {key: value for key, value in payload.items() if key != "request_id"}
    return hashlib.sha256(
        json.dumps(intent, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def canonical_runs(values):
    if not isinstance(values, (list, tuple)) or len(values) > 100000:
        raise ValueError("comparison_capacity_exceeded")
    return tuple(dict.fromkeys(str(UUID(value)) for value in values))


@dataclass(frozen=True)
class ComparisonRequest:
    query: AnalysisQuery
    mode: str
    run_ids: tuple[str, ...]
    excluded_run_ids: tuple[str, ...]
    detail_run_ids: tuple[str, ...]
    baseline_configuration: str | None
    request_id: str
    request_fingerprint: str

    @classmethod
    def parse(cls, payload, *, workspace_timezone=None):
        if not isinstance(payload, dict) or set(payload) - {
            "request_id",
            "filters",
            "grain",
            "timezone",
            "mode",
            "run_ids",
            "excluded_run_ids",
            "detail_run_ids",
            "baseline_configuration",
        }:
            raise ValueError("invalid_comparison_request")
        request_id = command_request_id(payload.get("request_id"))
        mode = payload.get("mode", "explicit")
        runs = canonical_runs(payload.get("run_ids", []))
        excluded = canonical_runs(payload.get("excluded_run_ids", []))
        details = canonical_runs(payload.get("detail_run_ids", []))
        if details:
            validate_detail_runs(details)
        if (
            mode not in {"explicit", "all_matching"}
            or (mode == "explicit" and (not runs or excluded))
            or (mode == "all_matching" and runs)
        ):
            raise ValueError("invalid_comparison_request")
        baseline = payload.get("baseline_configuration")
        return cls(
            AnalysisQuery.parse(
                payload.get("filters", {}),
                payload.get("grain", "day"),
                payload.get("timezone", "UTC"),
                workspace_timezone=workspace_timezone,
            ),
            mode,
            runs,
            excluded,
            details,
            str(UUID(baseline)) if baseline is not None else None,
            request_id,
            intent_fingerprint(payload),
        )


@dataclass(frozen=True)
class ComparisonRead:
    body: dict[str, Any]
    authority: AuthorityState


class ExecutionComparisonPort(Protocol):
    async def materialize(
        self,
        scope,
        principal,
        request: ComparisonRequest,
        *,
        comparison_id=None,
        expected_revision=None,
    ) -> tuple[str, int]:
        """RR, fixed facts/resources/members and detail retention, atomic publication."""
        ...

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
    ) -> ComparisonRead:
        """Reaggregate retained facts over currently authorized members only."""
        ...

    async def current(self, scope, principal, comparison_id, revision) -> AuthorityState:
        """Fresh READ COMMITTED authority + exact retained binding availability."""
        ...

    async def align(
        self, scope, principal, comparison_id, revision, *, expected_revision, edits, request_id
    ) -> int:
        """CAS and authored append, scoped visible retained endpoints, same transaction."""
        ...
