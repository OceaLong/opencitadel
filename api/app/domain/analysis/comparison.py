"""Evidence-preserving alignment over authorized, retained step identities."""

from collections import Counter, defaultdict
from dataclasses import dataclass

ALIGNMENT_VERSION = "step-alignment-v1"


@dataclass(frozen=True)
class StepIdentity:
    run_id: str
    step_id: str
    attempt_id: str | None
    cut: str
    kind: str
    tool_name: str | None = None
    case_revision: str | None = None
    semantic_key: str | None = None
    tool_contract_revision: str | None = None
    semantic_source: str | None = None
    provenance_scope: str | None = None
    parent_path: tuple[str, ...] = ()
    local_order: int | None = None
    parallel: bool = False

    @property
    def reference(self):
        return (self.run_id, self.step_id, self.attempt_id, self.cut)


@dataclass(frozen=True)
class Alignment:
    left: StepIdentity
    right: StepIdentity | None
    status: str
    provenance: str
    reason: str
    algorithm_version: str = ALIGNMENT_VERSION


def pair_status(left_key: str | None, right_key: str | None, unique: bool) -> str:
    return "suggested" if unique and left_key is not None and left_key == right_key else "unmatched"


def validate_detail_runs(run_ids):
    result = tuple(dict.fromkeys(run_ids))
    if not 1 <= len(result) <= 5:
        raise ValueError("comparison_detail_limit")
    return result


def _semantic(step):
    if (
        step.semantic_source != "fixed_case"
        or not step.case_revision
        or not step.semantic_key
        or not step.tool_contract_revision
    ):
        return None
    return (
        step.case_revision,
        step.semantic_key,
        step.kind,
        step.tool_name,
        step.tool_contract_revision,
    )


def _path(step):
    # A partially present/incompatible semantic assertion must not fall back to a guess.
    if (
        step.semantic_key is not None
        or step.parallel
        or not step.parent_path
        or step.local_order is None
    ):
        return None
    return (
        step.kind,
        step.tool_name,
        step.tool_contract_revision,
        step.parent_path,
        step.local_order,
    )


def suggest_alignments(left, right):
    if len(left) > 10000 or len(right) > 10000:
        raise ValueError("comparison_step_limit")
    indexes = []
    for key in (lambda step: step.reference, _semantic, _path):
        counts = Counter(key(step) for step in left)
        candidates = defaultdict(list)
        for step in right:
            candidates[key(step)].append(step)
        indexes.append((key, counts, candidates))
    result = []
    used = set()
    for step in left:
        match = None
        for (key, counts, candidates), status, source, reason in zip(
            indexes,
            ("explicit", "suggested", "suggested"),
            ("direct_reference", "fixed_case_semantic", "serial_path_heuristic"),
            (
                "Same Run, step, attempt and retained cut.",
                "Unique fixed case semantic key and compatible tool contract.",
                "Unique serial parent path and local order; requires confirmation.",
            ),
            strict=True,
        ):
            value = key(step)
            choices = candidates.get(value, [])
            if value is not None and counts[value] == 1 and len(choices) == 1:
                candidate = choices[0]
                if candidate.reference not in used:
                    if source == "fixed_case_semantic" and step.provenance_scope == "activity":
                        reason = "Unique recorded activity semantic binding; does not prove attempt consumption."
                    match = Alignment(step, candidate, status, source, reason)
                    used.add(candidate.reference)
                    break
        result.append(
            match
            or Alignment(
                step,
                None,
                "unmatched",
                "insufficient_evidence",
                "Missing, incompatible or ambiguous matching evidence.",
            )
        )
    return result


def retained_step_identities(body, cut):
    """Consume the repository's fixed ledger proof relation, never free-form StepView labels."""
    evidence = defaultdict(list)
    for row in body.get("semantic_evidence", []):
        evidence[row["step_id"], row["attempt_id"]].append(row)
    result = []
    for step in body["steps"]:
        rows = evidence[step["step_id"], step.get("attempt_id")]
        proof = rows[0] if len(rows) == 1 else {}
        result.append(
            StepIdentity(
                run_id=step["run_id"],
                step_id=step["step_id"],
                attempt_id=step.get("attempt_id"),
                cut=cut,
                kind=step["kind"],
                tool_name=step.get("tool_name"),
                case_revision=proof.get("case_revision"),
                semantic_key=proof.get("semantic_key"),
                tool_contract_revision=proof.get("tool_contract_revision"),
                semantic_source=proof.get("semantic_source"),
                provenance_scope=proof.get("provenance_scope"),
                # Public timestamps/order alone do not certify a serial parent path.
                parallel=True,
            )
        )
    return result


def apply_manual_alignments(suggestions, records, steps):
    """Apply authored pair projections in linear time, preserving other Run pairs."""
    from dataclasses import asdict

    index = {(step.run_id, step.step_id, step.attempt_id): step for step in steps}
    legacy = defaultdict(list)
    for step in steps:
        legacy[step.run_id, step.step_id].append(step)

    def selected(edit, side):
        key = edit[side + "_run_id"], edit[side + "_step_id"]
        if side + "_attempt_id" in edit:
            return index.get((*key, edit[side + "_attempt_id"]))
        choices = legacy[key]
        return choices[0] if len(choices) == 1 else None

    result = dict(enumerate(suggestions))
    by_endpoint = defaultdict(set)
    for identifier, row in result.items():
        pair = tuple(sorted(row["run_pair"]))
        for endpoint in (row["left"], row["right"]):
            if endpoint:
                by_endpoint[
                    pair, endpoint["run_id"], endpoint["step_id"], endpoint["attempt_id"]
                ].add(identifier)
    sequence = len(result)
    for record in records:
        edit = record["edit"]
        left = selected(edit, "left")
        right = selected(edit, "right")
        if left is None or right is None:
            continue
        pair = tuple(sorted((left.run_id, right.run_id)))
        touched = (
            (pair, left.run_id, left.step_id, left.attempt_id),
            (pair, right.run_id, right.step_id, right.attempt_id),
        )
        for key in touched:
            for identifier in by_endpoint.pop(key, set()):
                result.pop(identifier, None)
        item = asdict(
            Alignment(
                left,
                right if edit["action"] == "confirm" else None,
                "confirmed" if edit["action"] == "confirm" else "unmatched",
                "manual",
                "Confirmed by the recorded author."
                if edit["action"] == "confirm"
                else "Unpaired by the recorded author.",
            )
        )
        item.update(
            run_pair=list(pair),
            **{key: record[key] for key in ("revision", "supersedes", "author", "created_at")},
        )
        result[sequence] = item
        for key in touched:
            by_endpoint[key].add(sequence)
        sequence += 1
    return list(result.values())
