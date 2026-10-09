"""Finite per-sample public rows for the full synthetic population.

The caller consumes one bundle and drops it before advancing. These rows are
test inputs; only PackageSession may decide whether the full package qualifies.
"""

from capacity_population import SampleSlot
from capacity_population_window import SECOND

GIB = 1024**3


def sample_bundle(slot: SampleSlot):
    number = slot.ordinal
    operation = slot.operation
    admission = operation == "admission"
    live = operation == "live_visible"
    cold = slot.mode == "cold"
    window_id = slot.loaded_window_id
    start = (number * 50 + 20) * SECOND
    sequence = slot.operation_ordinal % 54 + 1 if live else None
    if live:
        start = 20 * SECOND + (sequence - 1) * 500_000_000
    duration = 100_000_000 if admission and slot.mode == "baseline" else 1_000_000
    if admission and slot.mode != "baseline":
        duration = 110_000_000
    target = {
        "scope_id": slot.dimension,
        "run_id": (
            f"admission-{number}"
            if admission
            else f"run-{number}-0"
            if live
            else "probe-run"
            if slot.dimension == "step_capacity"
            else "standard-run"
        ),
        "public_id": "completed-batch" if operation == "matrix" else "opaque-cut",
        "revision": "1",
        "step_id": "step" if operation in {"switch", "history", "live_visible"} else None,
    }
    if live:
        target["scope_id"] = "live"
    action = f"action-{slot.sample_id}"
    page = f"page-{slot.sample_id}"
    context = f"context-{number}-0"
    event = f"event-{number}-0-{sequence}" if live else None
    plan = {
        "sample_id": slot.sample_id,
        "dimension": slot.dimension,
        "mode": slot.mode,
        "operation": operation,
        "ordinal": slot.operation_ordinal,
        "target": target,
        "window_id": window_id,
        "physical_window_id": slot.physical_window_id,
        "reset_id": slot.reset_id,
        "prewarm_completed_ns": 2 if slot.mode == "warm" else None,
        "action_id": action,
        "page_id": page,
        "context_id": context,
        "live_resource_interval": {"offset_ns": SECOND, "duration_ns": 2 * SECOND}
        if live
        else None,
    }
    sample = {
        "sample_id": slot.sample_id,
        "clock_id": "host-clock",
        "start_ns": start,
        "end_ns": start + duration,
        "status": "ok",
        "error": None,
        "action_id": action,
        "trigger_ns": start,
        "source_id": slot.sample_id,
        "browser_id": None if admission else slot.sample_id,
    }
    kind, completion = {
        "first_screen": ("interactive_summary", "summary_interactive"),
        "switch": ("selected_step", "selected_step_painted"),
        "history": ("history_revision", "history_painted"),
        "analysis": ("analysis_capture", "analysis_displayed"),
        "matrix": ("matrix_page", "matrix_usable"),
        "live_visible": ("progress", "progress_painted"),
        "admission": ("formal_admission", None),
    }[operation]
    source = {
        "source_id": slot.sample_id,
        "sample_id": slot.sample_id,
        "target": target,
        "clock_id": "host-clock",
        "submitted_ns": start + 1000,
        "acknowledged_ns": start + 2000,
        "readback_ns": start + 3000,
        "kind": kind,
        "public_event_id": event,
        "sequence": sequence,
        "progress_id": event,
        "marker_id": f"marker-{number}-{sequence + 20}" if live else None,
        "captured_runs": 100_000 if operation == "analysis" else 0,
        "matrix_results": 5000 if operation == "matrix" else 0,
        "admission_session_id": f"admission-session-{number}" if admission else None,
        "admission_run_id": target["run_id"] if admission else None,
        "profile": "finite-text-120x500ms-v1" if live else "acceptance-capacity",
        "policy_id": "policy",
    }
    browser = None
    resource = None
    if not admission:
        browser = {
            "browser_id": slot.sample_id,
            "sample_id": slot.sample_id,
            "action_id": action,
            "page_id": page,
            "context_id": context,
            "clock_id": "host-clock",
            "readback_target": target,
            "painted_target": target,
            "public_event_id": event,
            "sequence": sequence,
            "readback_observed_ns": start + 4000,
            "paint_observed_ns": start + duration,
            "completion": completion,
            "visible": True,
        }
        capture_start = 20 * SECOND + SECOND // 2 if live else start
        capture_end = capture_start + (3 if live else 2) * SECOND
        sample_times = (
            [capture_start, 21 * SECOND + 200_000_000, capture_end]
            if live
            else [capture_start + 500_000_000]
        )
        resource = {
            "sample_id": slot.sample_id,
            "clock_id": "host-clock",
            "context_id": context,
            "renderer_id": f"renderer-{number}",
            "start_ns": capture_start,
            "end_ns": capture_end,
            "visible": True,
            "forced_gc": False,
            "scroll_start_ns": capture_start,
            "scroll_end_ns": capture_start + SECOND,
            "scroll_distance_px": 10000,
            "frames": [
                {"observed_ns": capture_start + frame * 16_000_000, "interval_ms": 16}
                for frame in range(188 if live else 100)
            ],
            "heap_samples": [
                {"observed_ns": stamp, "bytes": 150 * 1024**2} for stamp in sample_times
            ],
            "dom_samples": [{"observed_ns": stamp, "rows": 150} for stamp in sample_times],
            "long_tasks": [],
        }
    reset = reset_row(slot, start) if cold else None
    return plan, sample, source, browser, resource, reset


def reset_row(slot: SampleSlot, start: int):
    if slot.reset_id is None or slot.loaded_window_id is None:
        raise ValueError("cold reset requires a unique loaded window")

    def node(name, driver, child=None, backing=None, readonly=False, inode=None, image=None):
        return {
            "node_name": name,
            "driver": driver,
            "child": child,
            "backing": backing,
            "cache_direct": True,
            "cache_no_flush": False,
            "read_only": readonly,
            "device": 1 if inode else None,
            "inode": inode,
            "fd_direct": True if inode else None,
            "image_id": image,
        }

    number = slot.ordinal
    return {
        "reset_id": slot.reset_id,
        "sample_id": slot.sample_id,
        "window_id": slot.loaded_window_id,
        "seal_id": "seal",
        "process_id": number + 1,
        "process_start_ticks": number + 1,
        "process_uuid": f"process-{number}",
        "socket_peer_pid": number + 1,
        "socket_device": 1,
        "socket_inode": number + 500,
        "executable_sha256": "a" * 64,
        "argv_digest": "a" * 64,
        "boot_id": f"boot-{number}",
        "coordinator_clock_id": "host-clock",
        "launched_ns": start - 12 * SECOND,
        "minimal_ready_ns": start - 10 * SECOND,
        "load_ready_ns": start - 2 * SECOND,
        "target_dispatch_ns": start,
        "stopped_ns": start + 31 * SECOND,
        "kvm_enabled": True,
        "ram_resumed": False,
        "shared_mounts": [],
        "qmp_version": "10.1",
        "qmp_commands": [
            "query-uuid",
            "query-kvm",
            "query-cpus-fast",
            "query-memory-size-summary",
            "query-block",
            "query-named-block-nodes",
            "query-qmp-schema",
        ],
        "qmp_uuid": f"process-{number}",
        "guest_cpu_count": 16,
        "guest_memory_bytes": 32 * GIB,
        "nodes": [
            node("overlay", "qcow2", "overlay-file", "base"),
            node("base", "raw", "base-file", readonly=True),
            node("base-file", "file", readonly=True, inode=1, image="root"),
            node("overlay-file", "file", inode=number + 100),
        ],
        "target_reads_before_dispatch": [],
        "incidental_warming": ["catalog/load background writes"],
        "helper_module": "scripts.execution_capacity.guest_main",
        "helper_action": "cold-window",
        "helper_nonce": f"helper-{number}",
        "helper_response_nonce": f"helper-{number}",
        "helper_source_digest": "a" * 64,
    }
