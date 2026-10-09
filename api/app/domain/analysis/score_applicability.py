"""Immutable rubric applicability, independent of scoring completion.

Only metadata in the authorized, revision-cut projection is evidence. Case revision
and rubric identities are immutable; no other case or rubric can fill a gap.
"""


def normalize_score_records(records):
    evidence = {}
    for row in records:
        key = (row["dataset_version"], row["case_id"], row["rubric"])
        for source_set in row["source_sets"]:
            dimensions = tuple(sorted(set(source_set["applicable_dimensions"])))
            if key in evidence and evidence[key] != dimensions:
                raise ValueError("analysis_applicability_unavailable")
            evidence[key] = dimensions
    normalized = []
    for original in records:
        key = (original["dataset_version"], original["case_id"], original["rubric"])
        if key not in evidence:
            raise ValueError("analysis_applicability_unavailable")
        row = dict(original)
        dimensions = evidence[key]
        metadata = {item["source"]: item for item in row["source_sets"]}
        row["applicable"] = dict.fromkeys(("rule", "model", "human"), dimensions)
        # Rule IDs are not rubric dimensions. Only actual rule metadata certifies
        # rule completion; inferred applicability never fabricates a source set.
        row["required"] = {"rule": metadata.get("rule", {}).get("required_dimensions", [])}
        row["thresholds"] = {}
        for condition in row["required_conditions"]:
            kind, dimension = condition["source"], condition["dimension_id"]
            if kind == "rule" or dimension not in dimensions:
                continue
            row["required"].setdefault(kind, []).append(dimension)
            row["thresholds"].setdefault(kind, {})[dimension] = condition["minimum"]
        row["required_complete"] = "rule" in metadata and all(
            kind in metadata for kind, dims in row["required"].items() if dims
        )
        normalized.append(row)
    return normalized


def score_dimensions(row):
    """Native queries use the same applicability as scalar denominators."""
    dimensions = {
        (source, dimension)
        for source in ("model", "human")
        for dimension in row["applicable"][source]
    }
    dimensions.update(("rule", dimension) for dimension in row["required"]["rule"])
    dimensions.update(
        (score["source"], score["dimension"])
        for score in row["scores"]
        if score["source"] == "rule"
    )
    return dimensions
