"""Chart statistics from fixed authorized facts, never from bucket percentiles."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict

from app.domain.analysis.metrics import Metric, percentile, ratio

LATENCY_EDGES_MS = (0, 100, 500, 1000, 5000, 10000, 30000, 60000, 300000, 600000)
GROUP_FIELDS = ("family", "purpose", "execution_mode", "configuration_revision")


def latency(rows):
    observed = [
        r
        for r in rows
        if r["status"] in {"completed", "failed"}
        and r["duration_ms"] is not None
        and r["duration_ms"] >= 0
    ]
    missing = sum(r["status"] in {"completed", "failed"} for r in rows) - len(observed)
    excluded = sum(r["status"] not in {"completed", "failed"} for r in rows)
    values = [r["duration_ms"] for r in observed]
    counts = [0] * (len(LATENCY_EDGES_MS) - 1)
    overflow = []
    for value in values:
        index = bisect_right(LATENCY_EDGES_MS, value) - 1
        if index == len(counts):
            overflow.append(value)
        else:
            counts[index] += 1
    return {
        "scheme": "execution-latency-ms-v1",
        "edges_ms": list(LATENCY_EDGES_MS),
        "edge_convention": "lower_inclusive_upper_exclusive",
        "bin_counts": counts,
        "overflow": {
            "lower_ms": LATENCY_EDGES_MS[-1],
            "count": len(overflow),
            "maximum_ms": max(overflow) if overflow else None,
        },
        "p50": asdict(
            Metric(
                percentile(values, 0.5),
                "ms",
                sample_count=len(values),
                missing_count=missing,
                excluded_count=excluded,
            )
        ),
        "p95": asdict(
            Metric(
                percentile(values, 0.95),
                "ms",
                sample_count=len(values),
                missing_count=missing,
                excluded_count=excluded,
            )
        ),
        "samples": [
            {"run_id": r["run_id"], "duration_ms": r["duration_ms"]}
            for r in sorted(observed, key=lambda r: (r["duration_ms"], r["run_id"]))
        ]
        if len(values) < 20
        else [],
    }


def chart_metrics(facts):
    groups = defaultdict(list)
    for row in facts["runs"]:
        groups[tuple(row.get(k) for k in GROUP_FIELDS)].append(row)
    tools = facts.get("tools")
    return {
        "latency": latency(facts["runs"]),
        "latency_groups": [
            {"group": dict(zip(GROUP_FIELDS, key, strict=True)), **latency(rows)}
            for key, rows in groups.items()
        ],
        "tools": {
            "availability": "available" if tools is not None else "retained_data_unavailable",
            "items": []
            if tools is None
            else [
                {
                    "tool_name": row["tool_name"],
                    "error_rate": asdict(
                        ratio(row["errors"], row["terminal"], excluded=row["excluded"])
                    ),
                    **{
                        name: row[name]
                        for name in (
                            "terminal",
                            "errors",
                            "execution_errors",
                            "business_errors",
                            "excluded",
                            "unknown",
                            "deferred",
                            "cancelled",
                        )
                    },
                }
                for row in sorted(tools, key=lambda r: (-r["errors"], r["tool_name"] or ""))
            ],
        },
    }
