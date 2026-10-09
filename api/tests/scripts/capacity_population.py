"""Finite synthetic schedule for bounded consumer tests, never runtime proof.

Only the 1,200 immutable slot descriptors live together. Large public/private
record families must be emitted one window at a time by their owning writers.
"""

from dataclasses import dataclass

from scripts.acceptance.capacity_derive import dimensions


@dataclass(frozen=True, slots=True)
class SampleSlot:
    ordinal: int
    sample_id: str
    dimension: str
    mode: str
    operation: str
    operation_ordinal: int
    physical_window_id: str
    loaded_window_id: str | None
    reset_id: str | None


def slots():
    """Yield every required sample once in the shared immutable dimension order."""
    ordinal = 0
    for (dimension, mode, operation), count in dimensions().items():
        for operation_ordinal in range(count):
            sample_id = f"{dimension}-{mode}-{operation}-{operation_ordinal}"
            physical_window_id = f"physical-{ordinal:04}"
            yield SampleSlot(
                ordinal=ordinal,
                sample_id=sample_id,
                dimension=dimension,
                mode=mode,
                operation=operation,
                operation_ordinal=operation_ordinal,
                physical_window_id=physical_window_id,
                loaded_window_id=None if mode == "baseline" else physical_window_id,
                reset_id=f"reset-{ordinal:04}" if mode == "cold" else None,
            )
            ordinal += 1
