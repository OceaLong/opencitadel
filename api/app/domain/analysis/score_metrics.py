"""Case-equal source-set metrics. Callers partition immutable comparable strata first."""

from collections import defaultdict

from .metrics import Metric, case_means, case_weighted_mean, ratio, unique_rows


def score_metrics(rows):
    rows = unique_rows(rows, "result_id")
    if len({r["config_id"] for r in rows}) > 1:
        raise ValueError("configuration partition required")
    required_values, confirmed_values = [], []
    required_missing, required_excluded, pending = set(), set(), set()
    dimensions = defaultdict(list)
    for row in rows:
        case = row["case_id"]
        scores = {(s["source"], s["dimension"]): s for s in row["scores"]}
        if len(scores) != len(row["scores"]):
            raise ValueError("source dimension head must be unique")
        successful = row["execution_status"] == "succeeded"

        def valid(source, dimension, scores=scores):
            score = scores.get((source, dimension))
            return (
                score is not None
                and score["status"] == "valid"
                and not score.get("invalidated", False)
            )

        rules = row["required"].get("rule", [])
        if not successful or not rules:
            required_excluded.add(case)
        elif not all(valid("rule", d) for d in rules):
            required_missing.add(case)
        else:
            required_values.append(
                (case, float(all(scores["rule", d]["value"] is True for d in rules)))
            )
        confirmed = successful and row.get("required_complete", True)
        missing = successful and not row.get("required_complete", True)
        for source, dims in row["required"].items():
            for dimension in dims:
                if not valid(source, dimension):
                    missing |= successful
                    confirmed = False
                else:
                    value = scores[source, dimension]["value"]
                    threshold = row["thresholds"].get(source, {}).get(dimension)
                    if source == "rule":
                        confirmed &= value is True
                    elif threshold is None:
                        raise ValueError("required score threshold missing")
                    else:
                        confirmed &= value >= threshold
        if missing:
            pending.add(case)
        confirmed_values.append((case, float(confirmed)))
        dimensions_by_source = dict(row["applicable"])
        # E07 applicability names rubric dimensions; rule heads use independent rule:N IDs.
        if "rule" in dimensions_by_source or any(source == "rule" for source, _ in scores):
            dimensions_by_source["rule"] = sorted(
                {dimension for source, dimension in scores if source == "rule"}
                | set(row["required"].get("rule", ()))
            )
        for source, dims in dimensions_by_source.items():
            for dimension in dims:
                score = scores.get((source, dimension))
                excluded = not successful or bool(score and score.get("invalidated"))
                value = float(score["value"]) if successful and valid(source, dimension) else None
                dimensions[source, dimension].append((case, value, excluded))
    required = case_means(required_values)
    confirmed = case_means(confirmed_values)
    output = {
        "required_rule_pass_rate": ratio(
            sum(required.values()),
            len(required),
            missing=len(required_missing),
            excluded=len(required_excluded),
        ),
        "confirmed_pass_rate": ratio(sum(confirmed.values()), len(confirmed), missing=len(pending)),
    }
    for (source, dimension), values in dimensions.items():
        known = [(case, value) for case, value, _ in values if value is not None]
        missing = {case for case, value, excluded in values if value is None and not excluded}
        excluded = {case for case, _, excluded in values if excluded}
        means = case_means(known)
        prefix = source + ":" + dimension
        output[prefix + ":mean"] = Metric(
            case_weighted_mean(known),
            "boolean" if source == "rule" else "score_0_4",
            sample_count=len(means),
            missing_count=len(missing),
            excluded_count=len(excluded),
        )
        coverage = case_means(
            [
                (case, float(value is not None))
                for case, value, is_excluded in values
                if not is_excluded
            ]
        )
        output[prefix + ":coverage"] = ratio(
            sum(coverage.values()), len(coverage), excluded=len(excluded)
        )
        for value in range(2 if source == "rule" else 5):
            frequencies = case_means([(case, float(score == value)) for case, score in known])
            output[prefix + ":distribution:" + str(value)] = ratio(
                sum(frequencies.values()),
                len(frequencies),
                missing=len(missing),
                excluded=len(excluded),
            )
    return output
