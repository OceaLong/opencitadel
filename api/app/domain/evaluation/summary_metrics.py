"""Pure E11 captured-row derivation shared with fixed export serialization."""

from decimal import Decimal

from app.domain.evaluation.summary import SummaryPoint


def complete_cost(row):
    usages = (row.subject_usage, row.judge_usage)
    if all(
        u is not None and u.calls == u.money_known and u.unresolved == 0 and u.money is not None
        for u in usages
    ):
        return sum((Decimal(u.money) for u in usages if u is not None), Decimal(0))
    return None


def derive_snapshot(snapshot):
    points = []
    for row in snapshot.rows:
        known_cost = complete_cost(row)
        cost = str(known_cost) if known_cost is not None else None
        points.append(
            SummaryPoint(
                excluded=row.invalidated,
                result_id=row.id,
                case_id=row.case_id,
                config_id=row.config_id,
                value=float(row.value) if row.value is not None else None,
                cost_usd=cost,
            )
        )

    def total_usage(kind):
        usages = [getattr(row, kind) for row in snapshot.rows if getattr(row, kind) is not None]
        if not usages:
            return None
        return {
            "calls": sum(u.calls for u in usages),
            "token_known": sum(u.token_known for u in usages),
            "money_known": sum(u.money_known for u in usages),
            "unresolved": sum(u.unresolved for u in usages),
            "tokens": sum(u.tokens for u in usages if u.tokens is not None)
            if any(u.tokens is not None for u in usages)
            else None,
            "money": str(sum((Decimal(u.money) for u in usages if u.money is not None), Decimal(0)))
            if any(u.money is not None for u in usages)
            else None,
        }

    scoring_counts = {
        status: sum(row.scoring_status == status for row in snapshot.rows)
        for status in {row.scoring_status for row in snapshot.rows}
    }
    groups = {}
    for row in snapshot.rows:
        groups.setdefault((row.case_id, row.config_id), []).append(row)
    valid_groups = sum(any(r.value is not None for r in rows) for rows in groups.values())
    excluded_groups = sum(all(r.invalidated for r in rows) for rows in groups.values())
    return {
        "points": tuple(points),
        "allocations": snapshot.allocations,
        "distribution_metadata": {
            "sample_count": valid_groups,
            "missing_count": len(groups) - valid_groups - excluded_groups,
            "excluded_count": excluded_groups,
            "grain": "case_config",
            "watermark": snapshot.captured_at,
        },
        "quality_cost_metadata": {
            "sample_count": sum(p.value is not None and p.cost_usd is not None for p in points),
            "missing_count": sum(
                (p.value is None or p.cost_usd is None) and not r.invalidated
                for p, r in zip(points, snapshot.rows, strict=True)
            ),
            "excluded_count": sum(r.invalidated for r in snapshot.rows),
            "grain": "case_result",
            "watermark": snapshot.usage_watermark,
        },
        "scoring_counts": scoring_counts,
        "subject_usage": total_usage("subject_usage"),
        "judge_usage": total_usage("judge_usage"),
    }
