"""Configuration contrasts within immutable comparable strata only."""

from dataclasses import dataclass
from random import Random

from .metrics import case_means, percentile


def comparable_key(row) -> tuple:
    fields = (
        "family",
        "dataset_version",
        "mode",
        "environment_version",
        "rubric",
        "metric_version",
        "source",
        "dimension",
    )
    if any(row.get(field) is None for field in fields) or "applicable_dimensions" not in row:
        raise ValueError("missing comparability identity")
    return (
        *tuple(row[field] for field in fields),
        tuple(sorted(set(row["applicable_dimensions"]))),
    )


@dataclass(frozen=True)
class PairedComparison:
    mean_left: float | None
    mean_right: float | None
    delta: float | None
    relative_delta: float | None
    case_count: int
    confidence_interval: tuple[float, float] | None
    bootstrap_samples: int
    seed: int = 0
    interpretation: str = "paired_descriptive"


def paired_case_comparison(left, right):
    left, right = case_means(left), case_means(right)
    cases = sorted(left.keys() & right.keys())
    count = len(cases)
    if not count:
        return PairedComparison(None, None, None, None, 0, None, 0)
    mean_left = sum(left[k] for k in cases) / count
    mean_right = sum(right[k] for k in cases) / count
    deltas = [left[k] - right[k] for k in cases]
    delta = sum(deltas) / count
    interval, samples = None, 0
    if count >= 20:
        random = Random(0)
        samples = 2000
        bootstraps = [
            sum(deltas[random.randrange(count)] for _ in cases) / count for _ in range(samples)
        ]
        interval = (percentile(bootstraps, 0.025), percentile(bootstraps, 0.975))
    return PairedComparison(
        mean_left,
        mean_right,
        delta,
        delta / mean_right if mean_right else None,
        count,
        interval,
        samples,
    )
