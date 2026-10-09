"""Source metadata partitions and case-paired configuration summaries."""

from collections import defaultdict
from dataclasses import asdict
from itertools import combinations

from .comparability import comparable_key, paired_case_comparison
from .metrics import METRIC_VERSION
from .score_applicability import normalize_score_records
from .score_metrics import score_metrics


def score_summary(records, *, selection=(), allow_automatic=True):
    if (
        len(selection) > 5
        or len(set(selection)) != len(selection)
        or not set(selection) <= {row["config_id"] for row in records}
    ):
        raise ValueError("analysis_comparison_selection_unavailable")
    groups, comparable = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for row in normalize_score_records(records):
        key = tuple(
            row[name]
            for name in ("family", "dataset_version", "mode", "environment_version", "rubric")
        )
        applicability = tuple(
            sorted((kind, tuple(sorted(dims))) for kind, dims in row["applicable"].items())
        )
        groups[key, applicability, row["config_id"]].append(row)
        for score in row["scores"]:
            if (
                row["execution_status"] != "succeeded"
                or score["status"] != "valid"
                or score["invalidated"]
            ):
                continue
            kind, dimension = score["source"], score["dimension"]
            comparison_key = comparable_key(
                {
                    **row,
                    "source": kind,
                    "dimension": dimension,
                    "metric_version": METRIC_VERSION,
                    "applicable_dimensions": row["applicable"].get(kind, []),
                }
            )
            comparable[comparison_key][row["config_id"]].append(
                (row["case_id"], float(score["value"]))
            )
    series = []
    for (key, applicability, config), rows in sorted(groups.items()):
        series.append(
            {
                "identity": key,
                "applicable_dimensions": applicability,
                "configuration": config,
                "metrics": {name: asdict(value) for name, value in score_metrics(rows).items()},
            }
        )
    if len(selection) > 1:
        compatible_members = defaultdict(set)
        for key, applicability, config in groups:
            compatible_members[key, applicability].add(config)
        if not any(set(selection) <= members for members in compatible_members.values()):
            raise ValueError("analysis_comparison_selection_unavailable")
    comparisons = []
    required = []
    for key, configurations in sorted(comparable.items()):
        if not selection and not allow_automatic:
            continue
        if selection:
            configurations = {k: v for k, v in configurations.items() if k in selection}
        if len(configurations) > 5:
            required.append({"identity": key, "status": "comparison_selection_required"})
            continue
        for left, right in combinations(sorted(configurations), 2):
            comparisons.append(
                {
                    "identity": key,
                    "left": left,
                    "right": right,
                    **asdict(paired_case_comparison(configurations[left], configurations[right])),
                }
            )
    cuts = sorted(
        {(row["batch_id"], row["evaluation_revision"]) for row in records if "batch_id" in row}
    )
    result = {
        "series": series,
        "comparisons": comparisons,
        "comparison_selection_required": required,
        "evaluation_cuts": [
            {"batch_id": batch, "evaluation_revision": revision} for batch, revision in cuts
        ],
    }
    if not allow_automatic:
        result["selection_status"] = "available" if selection else "unavailable"
    return result
