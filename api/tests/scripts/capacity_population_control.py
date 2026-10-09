"""Finite Protocol/Network singleton writer for synthetic source-only checks.

The resulting bytes are test inputs, never host or native provenance.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from api.tests.scripts.capacity_population import slots
from api.tests.scripts.capacity_population_sample import sample_bundle
from api.tests.scripts.capacity_population_window import SECOND, network_rows, window_plan
from api.tests.scripts.capacity_population_writer import FiniteRoleWriter
from scripts.acceptance.capacity_io import MAX_ARTIFACT_BYTES


def _singleton(root: Path, role: str, controls: dict, families):
    if (
        not root.is_absolute()
        or not root.is_dir()
        or stat.S_IMODE(root.stat().st_mode) != 0o700
        or role != "protocol"
        or controls.get("schema_version") != 3
        or controls.get("role") != role
    ):
        raise ValueError("private finite singleton source required")
    path = f"{role}.json"
    descriptor = os.open(root / path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    digest = hashlib.sha256()
    size = 0

    def emit(raw):
        nonlocal size
        if size + len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("finite singleton exceeds product artifact limit")
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:
                raise OSError("finite singleton write made no progress")
            written += count
        digest.update(raw)
        size += len(raw)

    try:
        prefix = json.dumps(controls, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if not prefix.endswith("}") or len(prefix) > 1024 * 1024:
            raise ValueError("finite singleton controls exceed bound")
        emit(prefix[:-1].encode())
        for field, rows in families:
            if field in controls:
                raise ValueError("finite singleton scalar/list overlap")
            emit(b',"' + field.encode() + b'":[')
            first = True
            for row in rows:
                raw = json.dumps(
                    row, ensure_ascii=True, separators=(",", ":"), allow_nan=False
                ).encode()
                if len(raw) > 1024 * 1024:
                    raise ValueError("finite singleton row exceeds working bound")
                if not first:
                    emit(b",")
                emit(raw)
                first = False
            emit(b"]")
        emit(b"}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "path": path,
        "sha256": digest.hexdigest(),
        "size_bytes": size,
        "role": role,
        "schema_version": 3,
        "shard_index": 0,
        "shard_count": 1,
    }


def _link(namespace, ifindex, peer, address):
    return {
        "namespace_inode": namespace,
        "ifindex": ifindex,
        "peer_ifindex": peer,
        "address": address,
        "route_destination": "192.0.2.0/30",
        "route_ifindex": ifindex,
        "qdisc_kind": "netem",
        "delay_us": 25000,
        "rate_bps": 20000000,
        "default_route": False,
    }


def write_control_roles(
    root, *, attempt_id, protocol_id, seal_id, binding_digest, slot_factory=slots
):
    """Emit all registered sample/window/network rows without role-wide lists."""
    common = {
        "schema_version": 3,
        "attempt_id": attempt_id,
        "protocol_id": protocol_id,
    }
    protocol = _singleton(
        root,
        "protocol",
        {
            **common,
            "role": "protocol",
            "binding_digest": binding_digest,
            "seal_id": seal_id,
            "registered_ns": 1,
            "clock_id": "host-clock",
            "clock_unit": "nanoseconds",
            "latency_method": "coordinator_predispatch_to_paint_upper_bound",
            "rate_rule": "each-1s-bin-at-least-2-v1",
            "startup_ns": 10 * SECOND,
            "marker_cadence_ns": 500_000_000,
            "marker_timeout_ns": 100_000_000,
            "marker_count_bound": 10_000,
            "marker_max_outstanding": 1,
            "load_ready_ns": 2 * SECOND,
            "warm_plan": "same-target-explicit-prewarm-before-window",
            "diagnostics_timing": "after-timed-read-or-separate-clone",
            "backend": "linux-x86_64-kvm-qemu",
            "network_backend": "owned-netns-veth-qemu-usernet",
        },
        (
            ("samples", (sample_bundle(slot)[0] for slot in slot_factory())),
            (
                "windows",
                (window_plan(slot) for slot in slot_factory() if slot.loaded_window_id is not None),
            ),
        ),
    )
    network = FiniteRoleWriter(
        root,
        "network",
        controls={
            **common,
            "role": "network",
            "backend": "owned-netns-veth-qemu-usernet",
            "host_link": _link(1, 1, 2, "192.0.2.1"),
            "client_link": _link(2, 2, 1, "192.0.2.2"),
            "forward_bound_address": "192.0.2.1",
            "guest_ports": [8000],
            "global_ip_forward_changed": False,
            "nat_changed": False,
            "cdp_delay_ms": 0,
            "validity": {
                "added_rtt_min_ns": 49_000_000,
                "added_rtt_max_ns": 51_000_000,
                "throughput_min_bps": 19_000_000,
                "throughput_max_bps": 21_000_000,
            },
        },
        list_fields=("context_ids", "calibrations", "phase_intervals"),
    )
    for field, rows in (
        (
            "context_ids",
            (
                f"context-{slot.ordinal}-{index}"
                for slot in slot_factory()
                if slot.loaded_window_id is not None
                for index in range(10)
            ),
        ),
        (
            "calibrations",
            (
                row
                for slot in slot_factory()
                if slot.loaded_window_id is not None
                for kind, row in network_rows(slot)
                if kind == "calibration"
            ),
        ),
        (
            "phase_intervals",
            (
                row
                for slot in slot_factory()
                if slot.loaded_window_id is not None
                for kind, row in network_rows(slot)
                if kind == "phase"
            ),
        ),
    ):
        network.start(field)
        for row in rows:
            network.append(field, row)
        network.finish_field(field)
    return protocol, *network.close()
