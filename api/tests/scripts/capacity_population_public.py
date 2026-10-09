"""Bounded public shard emission from immutable full-population slots.

This is a partial artifact producer. Diagnostics original captures, C2c and
native host receipts must be supplied by their independent authorities before
the package can be validated as a complete source.
"""

from capacity_population import slots
from capacity_population_sample import sample_bundle
from capacity_population_window import marker_rows, progress_rows, window_bundle
from capacity_population_writer import FiniteRoleWriter


def _role(root, name, fields, *, controls=None):
    return FiniteRoleWriter(
        root,
        name,
        controls={
            "schema_version": 3,
            "attempt_id": "synthetic-attempt",
            "protocol_id": "synthetic-protocol",
            "role": name,
            **(controls or {}),
        },
        list_fields=fields,
    )


def _emit(writer, field, rows):
    writer.start(field)
    for row in rows:
        writer.append(field, row)
    writer.finish_field(field)


def write_public_sharded_roles(root, *, slot_factory=slots):
    """Emit workload/measurements/resets without retaining a role-wide list.

    Each family rereads the immutable slot schedule; a window or sample bundle
    is dropped before the next slot. An empty role remains an explicit error.
    """
    workload = _role(root, "workload", ("windows", "progress", "source_acks", "errors"))
    _emit(
        workload,
        "windows",
        (window_bundle(slot)[1] for slot in slot_factory() if slot.loaded_window_id is not None),
    )
    _emit(
        workload,
        "progress",
        (
            row[0]
            for slot in slot_factory()
            if slot.loaded_window_id is not None
            for row in progress_rows(slot)
        ),
    )
    _emit(
        workload,
        "source_acks",
        (
            row[1]
            for slot in slot_factory()
            if slot.loaded_window_id is not None
            for row in progress_rows(slot)
        ),
    )
    workload_descriptors = workload.close()

    measurements = _role(
        root,
        "measurements",
        (
            "native_bindings",
            "markers",
            "live_paints",
            "samples",
            "sources",
            "browsers",
            "resources",
            "errors",
        ),
    )
    _emit(measurements, "native_bindings", ())
    _emit(
        measurements,
        "markers",
        (
            row
            for slot in slot_factory()
            if slot.loaded_window_id is not None
            for row in marker_rows(slot)
        ),
    )
    _emit(
        measurements,
        "live_paints",
        (
            row[2]
            for slot in slot_factory()
            if slot.loaded_window_id is not None
            for row in progress_rows(slot)
        ),
    )
    for field, position in (("samples", 1), ("sources", 2), ("browsers", 3), ("resources", 4)):
        _emit(
            measurements,
            field,
            (
                value
                for slot in slot_factory()
                if (value := sample_bundle(slot)[position]) is not None
            ),
        )
    measurement_descriptors = measurements.close()

    resets = _role(root, "resets", ("resets",))
    _emit(
        resets,
        "resets",
        (value for slot in slot_factory() if (value := sample_bundle(slot)[5]) is not None),
    )
    reset_descriptors = resets.close()
    return workload_descriptors + measurement_descriptors + reset_descriptors
