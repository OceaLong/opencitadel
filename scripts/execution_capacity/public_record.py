"""Materialize only one complete public record under the existing wire ceiling.

This admission is separate from individual original-row limits. The fixed 64E
reservation is provisional accounting for raw JSON/decoded/model/export copies; the full
capacity derivation and resident-memory validation remain explicit audit gates.
"""

from scripts.acceptance.capacity_io import MAX_ARTIFACT_BYTES, strict_json
from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError
from scripts.execution_capacity.evidence_json import chunks


def public_record_value(value, *, owner, budget, cohort_kind=None):
    def parts():
        if cohort_kind is None:
            yield from chunks(value, owner=owner, budget=budget)
            return
        if (
            cohort_kind not in ("base", "round")
            or type(value) is not dict
            or "cohorts" not in value
        ):
            raise ValueError("fixed public cohort record required")
        budget.reserve(len(value) * 128, rows=0, largest=len(value) * 128)
        yield b"{"
        for position, key in enumerate(sorted(value)):
            if position:
                yield b","
            yield from chunks(key, owner=owner, budget=budget)
            yield b":"
            if key != "cohorts":
                yield from chunks(value[key], owner=owner, budget=budget)
                continue
            yield b"["
            count = 0
            for row in value[key]:
                if row["origin"]["kind"] == cohort_kind:
                    if count:
                        yield b","
                    yield from chunks(row, owner=owner, budget=budget)
                    count += 1
            yield b"]"
        yield b"}"

    size = 0
    for part in parts():
        size += len(part)
        if size > MAX_ARTIFACT_BYTES:
            raise EvidenceQuotaError("complete public record exceeds existing artifact limit")
    # Original rows retain their own row ceiling; this is the complete public
    # wire record, governed by MAX_ARTIFACT_BYTES, not a private original row.
    budget.reserve(64 * size, rows=1)
    raw = bytearray()
    for part in parts():
        if len(raw) + len(part) > size:
            raise ValueError("complete public record changed during admission")
        raw.extend(part)
    if len(raw) != size:
        raise ValueError("complete public record changed during admission")
    return strict_json(raw)
