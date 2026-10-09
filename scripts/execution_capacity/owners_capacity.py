"""Necessary lower bounds for an OWNERS source read, never capacity approval.

The immutable evidence Plan supplies retention limits but no population ceiling,
full call count, or independent cumulative-work limits. This module performs
only arithmetic; it neither dispatches SQL nor interprets a retained report.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

from scripts.acceptance.capacity_derive import dimensions
from scripts.execution_capacity.evidence_bounds import PHASE_BYTES, PHASE_ROWS
from scripts.execution_capacity.guest_seal_entry import parse_evidence_limits

_MAX_INTEGER = 2**63 - 1
_STANDARD_OWNER_ROWS = 100_000
_QUERY_BYTES_PER_OWNER = 64 + 4096  # T >= N in Q_bytes = 64T + 4096N.
_QUERY_ROWS_PER_OWNER = 1 + 16  # T >= N in Q_rows = T + 16N.


def _bounded_integer(value: int, *, name: str, allow_zero: bool = False) -> int:
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= _MAX_INTEGER:
        raise ValueError(f"{name} must be a bounded integer")
    return value


def _bounded_product(left: int, right: int, *, name: str) -> int:
    if left and right > _MAX_INTEGER // left:
        raise ValueError(f"{name} exceeds supported integer bound")
    return left * right


@dataclass(frozen=True)
class FrozenOwnerBound:
    """A rejection or unresolved necessary condition, never success evidence."""

    status: Literal["infeasible", "unproven"]
    source_reads: int
    source_reads_basis: Literal["diagnostic-input", "conditional-protocol-floor"]
    source_reads_plan_bound: Literal[False]
    diagnostic_page_size: int | None
    standard_owner_rows: int
    additional_owner_rows: int
    owner_rows_lower_bound: int
    session_bytes_limit: int
    session_rows_limit: int
    permanent_rows_lower_bound_per_read: int
    query_bytes_lower_bound_per_read: int
    query_rows_lower_bound_per_read: int
    summed_query_bytes_lower_bound: int
    summed_query_rows_lower_bound: int
    blockers: tuple[str, ...]
    unresolved: tuple[str, ...]


def assess_owners_lower_bound(
    evidence_limits: Mapping[str, int],
    *,
    source_reads: int,
    additional_owner_rows: int = 0,
    diagnostic_page_size: int | None = None,
) -> FrozenOwnerBound:
    """Evaluate necessary bounds for current OWNERS, using diagnostic counts.

    ``source_reads`` and ``additional_owner_rows`` are not authenticated Plan
    facts. The result always records that limitation; it never authorizes a
    read. A page size changes neither permanent per-row charges nor summed Q.
    """

    limits = parse_evidence_limits(evidence_limits)
    reads = _bounded_integer(source_reads, name="source_reads")
    additional = _bounded_integer(
        additional_owner_rows, name="additional_owner_rows", allow_zero=True
    )
    if diagnostic_page_size is not None:
        diagnostic_page_size = _bounded_integer(diagnostic_page_size, name="diagnostic_page_size")
    if additional > _MAX_INTEGER - _STANDARD_OWNER_ROWS:
        raise ValueError("owner row lower bound exceeds supported integer bound")
    owners = _STANDARD_OWNER_ROWS + additional
    query_bytes = _bounded_product(owners, _QUERY_BYTES_PER_OWNER, name="query bytes")
    query_rows = _bounded_product(owners, _QUERY_ROWS_PER_OWNER, name="query rows")
    total_bytes = _bounded_product(query_bytes, reads, name="summed query bytes")
    total_rows = _bounded_product(query_rows, reads, name="summed query rows")

    blockers = []
    if owners > PHASE_ROWS:
        blockers.append("permanent-owner-rows-exceed-fixed-session-child")
    # Current full OWNERS dispatch reserves Q permanently. Pagination would
    # remove this particular full-result comparison, not the P bound above.
    if query_bytes > PHASE_BYTES or query_rows > PHASE_ROWS:
        blockers.append("current-unpaged-query-exceeds-fixed-session-child")
    # EvidenceOwner.from_limits makes the root's default work limits equal to
    # these Plan limits. EvidenceBudget.child inherits those work limits; it
    # does NOT reset work to the fixed 16 MiB / 16,384 retention child limits.
    if query_bytes > limits["bytes_limit"] or query_rows > limits["rows_limit"]:
        blockers.append("leased-query-work-would-exceed-plan-inherited-work")

    return FrozenOwnerBound(
        status="infeasible" if blockers else "unproven",
        source_reads=reads,
        source_reads_basis="diagnostic-input",
        source_reads_plan_bound=False,
        diagnostic_page_size=diagnostic_page_size,
        standard_owner_rows=_STANDARD_OWNER_ROWS,
        additional_owner_rows=additional,
        owner_rows_lower_bound=owners,
        session_bytes_limit=PHASE_BYTES,
        session_rows_limit=PHASE_ROWS,
        permanent_rows_lower_bound_per_read=owners,
        query_bytes_lower_bound_per_read=query_bytes,
        query_rows_lower_bound_per_read=query_rows,
        summed_query_bytes_lower_bound=total_bytes,
        summed_query_rows_lower_bound=total_rows,
        blockers=tuple(blockers),
        unresolved=(
            "actual-additional-owner-upper-bound",
            "trusted-source-read-count",
            "all-sql-and-repeat-visit-work-bound",
            "result-last-use-and-retained-graph-bound",
            "independent-global-count-and-digest",
            "postgres-snapshot-collation-and-server-resource-proof",
        ),
    )


def assess_required_owners_floor(evidence_limits: Mapping[str, int]) -> FrozenOwnerBound:
    """Conditionally bound one OWNERS read per required protocol sample.

    The dimension sum is a protocol floor, not an observed inventory read
    count or an authenticated Plan bound. This cannot approve a source read,
    capacity result, or full-scale run.
    """

    bound = assess_owners_lower_bound(evidence_limits, source_reads=sum(dimensions().values()))
    return replace(
        bound,
        source_reads_basis="conditional-protocol-floor",
        unresolved=(*bound.unresolved, "one-owners-read-per-required-sample-assumption"),
    )
