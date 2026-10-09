"""Metrics over fixed public facts; missing evidence is never a zero measurement."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

METRIC_VERSION = "execution-analysis-v2"


@dataclass(frozen=True)
class Metric:
    value: float | int | Decimal | None
    unit: str
    numerator: float | int | None = None
    denominator: int | None = None
    sample_count: int = 0
    missing_count: int = 0
    excluded_count: int = 0


def ratio(numerator, denominator, *, missing=0, excluded=0):
    return Metric(
        numerator / denominator if denominator else None,
        "ratio",
        numerator,
        denominator,
        denominator,
        missing,
        excluded,
    )


def interval_union_ms(intervals: list[tuple[int, int]]) -> int:
    end, total = None, 0
    for left, right in sorted(intervals):
        if right < left:
            raise ValueError("negative interval")
        total += right - left if end is None else max(0, right - max(left, end))
        end = right if end is None else max(end, right)
    return total


def case_means(rows):
    cases = defaultdict(list)
    for key, value in rows:
        if not isfinite(value):
            raise ValueError("nonfinite score")
        cases[key].append(value)
    return {key: sum(values) / len(values) for key, values in cases.items()}


def case_weighted_mean(rows: list[tuple[str, float]]) -> float | None:
    values = list(case_means(rows).values())
    return sum(values) / len(values) if values else None


def percentile(values, probability):
    """Exact interpolated fixture/reference statistic; database aggregates use percentile_cont."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def unique_rows(rows, identity):
    unique = {}
    for row in rows:
        key = row[identity]
        if key in unique and unique[key] != row:
            raise ValueError("conflicting fixed identity")
        unique[key] = row
    return list(unique.values())


def execution_metrics(rows):
    rows = unique_rows(rows, "run_id")
    counts = Counter(row["status"] for row in rows)
    completed, failed = counts["completed"], counts["failed"]
    terminal = [row for row in rows if row["status"] in {"completed", "failed"}]
    samples = [
        r["duration_ms"]
        for r in terminal
        if r.get("duration_ms") is not None and r["duration_ms"] >= 0
    ]
    missing = len(terminal) - len(samples)
    result = {
        "run_count": Metric(len(rows), "run", sample_count=len(rows)),
        "success_rate": ratio(
            completed, completed + failed, excluded=len(rows) - completed - failed
        ),
        "latency_p50": Metric(
            percentile(samples, 0.5),
            "ms",
            sample_count=len(samples),
            missing_count=missing,
            excluded_count=len(rows) - len(terminal),
        ),
        "latency_p95": Metric(
            percentile(samples, 0.95),
            "ms",
            sample_count=len(samples),
            missing_count=missing,
            excluded_count=len(rows) - len(terminal),
        ),
    }
    for name, count in [
        ("completed", completed),
        ("failed", failed),
        ("cancelled", counts["cancelled"]),
        ("pending", sum(counts[s] for s in ("new", "queued", "running", "waiting"))),
        (
            "unknown",
            sum(
                n
                for s, n in counts.items()
                if s
                not in {"completed", "failed", "cancelled", "new", "queued", "running", "waiting"}
            ),
        ),
    ]:
        result[name] = Metric(count, "run", sample_count=len(rows))
    return result


def usage_metrics(rows):
    groups = defaultdict(list)
    for row in unique_rows(rows, "call_identity"):
        if row["purpose"] not in {
            "production",
            "evaluation_subject",
            "evaluation_judge",
            "unknown",
        }:
            raise ValueError("invalid usage purpose")
        groups[row["purpose"]].append(row)
    result = {}
    for purpose, calls in groups.items():
        metrics = {}
        for field, unit in [
            ("input_tokens", "token"),
            ("output_tokens", "token"),
            ("cost_usd", "USD"),
        ]:
            known = [row[field] for row in calls if row.get(field) is not None]
            if any(value < 0 for value in known):
                raise ValueError("negative usage")
            metrics[field] = Metric(
                sum(known) if known else None,
                unit,
                sample_count=len(known),
                missing_count=len(calls) - len(known),
            )
        metrics["token_coverage"] = ratio(
            sum(
                r.get("input_tokens") is not None and r.get("output_tokens") is not None
                for r in calls
            ),
            len(calls),
        )
        metrics["cost_coverage"] = ratio(
            sum(r.get("cost_usd") is not None for r in calls), len(calls)
        )
        result[purpose] = metrics
    return result


def attempt_metrics(rows, *, start_ms, cut_ms):
    rows = unique_rows(
        [r for r in rows if r.get("attempt_id") and r["status"] != "queued"], "attempt_id"
    )
    intervals, tool_durations = [], []
    missing = tool_missing = 0
    tools = [r for r in rows if r["kind"] == "tool"]
    for row in rows:
        left, right = row.get("started_ms"), row.get("ended_ms")
        if left is None or right is None or right < left:
            missing += 1
            tool_missing += row["kind"] == "tool"
            continue
        left, right = max(left, start_ms), min(right, cut_ms)
        if right < left:
            continue
        intervals.append((left, right))
        if row["kind"] == "tool":
            tool_durations.append(right - left)
    known = [r for r in tools if r["status"] in {"completed", "failed"}]
    errors = sum(
        r["status"] == "failed" or r.get("business_outcome") in {"failed", "failure"} for r in known
    )
    result = {
        "activity_occupancy_ms": Metric(
            interval_union_ms(intervals) if intervals else None,
            "ms",
            sample_count=len(intervals),
            missing_count=missing,
        ),
        "tool_work_ms": Metric(
            sum(tool_durations) if tool_durations else None,
            "ms",
            sample_count=len(tool_durations),
            missing_count=tool_missing,
        ),
        "tool_error_rate": ratio(errors, len(known), excluded=len(tools) - len(known)),
        "tool_execution_errors": Metric(
            sum(r["status"] == "failed" for r in known), "attempt", sample_count=len(known)
        ),
        "tool_business_errors": Metric(
            sum(r.get("business_outcome") in {"failed", "failure"} for r in known),
            "attempt",
            sample_count=len(known),
        ),
    }
    for state in ("unknown", "deferred", "cancelled"):
        result["tool_" + state] = Metric(
            sum(r["status"] == state for r in tools), "attempt", sample_count=len(tools)
        )
    return result


def approval_metrics(events, *, start_ms, cut_ms):
    pending, finished, intervals = {}, set(), []
    missing = 0
    for event in events:
        identity, at = event["approval_id"], event.get("occurred_ms")
        if identity in finished:
            continue
        if event["status"] in {"pending", "requested", "waiting"}:
            pending.setdefault(identity, at)
        elif event["status"] in {"approved", "rejected", "expired", "cancelled", "decided"}:
            left = pending.pop(identity, None)
            if left is None or at is None or at < left:
                missing += 1
            elif min(at, cut_ms) >= max(left, start_ms):
                intervals.append((max(left, start_ms), min(at, cut_ms)))
            finished.add(identity)
    for left in pending.values():
        if left is None or left > cut_ms:
            missing += 1
        else:
            intervals.append((max(left, start_ms), cut_ms))
    return Metric(
        interval_union_ms(intervals) if intervals else None,
        "ms",
        sample_count=len(intervals),
        missing_count=missing,
    )


def resolve_timezone(workspace_timezone, user_timezone):
    value = workspace_timezone or user_timezone or "UTC"
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("invalid_analysis_timezone") from None
    return value


def calendar_bucket(at: datetime, grain: str, timezone: str):
    if at.tzinfo is None:
        raise ValueError("aware instant required")
    local = at.astimezone(ZoneInfo(timezone))
    if grain == "hour":
        start = local.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
        return start, start + timedelta(hours=1)
    if grain == "day":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)
    raise ValueError("invalid time grain")
