"""Explicitly SYNTHETIC planned transcript. Never import this from a producer.

No browser, database, provider, process, network or machine is touched. These
public records have no private original proof and cannot form an acceptance
report; they are useful only for source-size and field-level test calculations.
"""

import copy
import hashlib
import json

from scripts.acceptance.capacity import FIXTURE_COUNTS
from scripts.acceptance.capacity_derive import dimensions
from scripts.acceptance.capacity_io import canonical_digest

D = "a" * 64
GIB = 1024**3
SECOND = 10**9


def transcript():
    roles = {}

    def role(name, **fields):
        value = dict(
            schema_version=3,
            attempt_id="synthetic-attempt",
            protocol_id="synthetic-protocol",
            role=name,
            **fields,
        )
        roles[name] = value
        return value

    fixture = {
        "schema_version": 1,
        "fixture_id": "synthetic-fixture",
        "seed": 5,
        "scope_ids": ["standard"],
        "counts": dict(FIXTURE_COUNTS),
        "window_start": "2026-06-19T00:00:00Z",
        "window_end": "2026-09-17T00:00:00Z",
        "status": "planned",
        "target": {
            "environment": "test",
            "runtime_id": "synthetic-runtime",
            "database_id": "synthetic-db",
            "scope_id": "standard",
        },
        "ownership_journal": "ownership.jsonl",
    }
    roles["fixture"] = fixture
    binding = {
        "revision": "a" * 40,
        "dirty_tree_digest": D,
        "images": {"api": "sha256:" + D},
        "migration": "0022",
        "fixture_manifest_digest": hashlib.sha256(json.dumps(fixture).encode()).hexdigest(),
    }
    protocol = role(
        "protocol",
        binding_digest=canonical_digest(binding),
        seal_id="seal",
        registered_ns=1,
        clock_id="host-clock",
        clock_unit="nanoseconds",
        latency_method="coordinator_predispatch_to_paint_upper_bound",
        rate_rule="each-1s-bin-at-least-2-v1",
        startup_ns=10 * SECOND,
        marker_cadence_ns=500000000,
        marker_timeout_ns=100000000,
        marker_count_bound=10000,
        marker_max_outstanding=1,
        load_ready_ns=2 * SECOND,
        warm_plan="same-target-explicit-prewarm-before-window",
        diagnostics_timing="after-timed-read-or-separate-clone",
        backend="linux-x86_64-kvm-qemu",
        network_backend="owned-netns-veth-qemu-usernet",
        samples=[],
        windows=[],
    )
    limits = {
        "workers": 3,
        "per_worker": 8,
        "claim_batch_size": 8,
        "pool_size": 5,
        "max_overflow": 5,
        "subject_concurrency": 5,
        "judge_concurrency": 2,
        "environment_concurrency": 5,
        "physical_global": 24,
        "physical_user": 24,
        "physical_provider": 24,
        "policy_id": "policy",
    }
    role(
        "environment",
        server_host_id="host",
        client_host_id="client",
        server_cpu_ids=list(range(16)),
        client_cpu_ids=list(range(4)),
        host_memory_bytes=64 * GIB,
        server_memory_bytes=32 * GIB,
        client_memory_bytes=16 * GIB,
        overhead_memory_bytes=GIB,
        postgres_memory_max_bytes=8 * GIB,
        cgroup_id="pg-cgroup",
        disk_id="ssd",
        disk_rotational=False,
        disk_direct_io=True,
        architecture="x86_64",
        os="Linux",
        browser="Chromium",
        postgres="PG",
        python="Python",
        playwright="Playwright",
        qemu_binary_sha256=D,
        viewport=[1440, 900],
        runtime_limits=limits,
    )

    def cohort(kind, runs, events, steps=0, ids=None):
        counts = {
            "runs": runs,
            "formal_events": events,
            "observations": events,
            "visible_steps": steps,
        }
        return {
            "cohort_id": kind,
            "kind": kind,
            "scope_id": kind,
            "run_ids": ids or [f"{kind}-{i}" for i in range(runs)],
            "origin": {
                "kind": "base",
                "seal_id": "seal",
                "round": None,
                "boot_id": None,
                "clone_id": None,
            },
            "source": counts,
            "view": dict(counts),
            "parity_digest": D,
        }

    cohorts = [
        cohort("standard", 100000, 10000000),
        cohort("step_capacity", 1, 30003, 10000, ["probe-run"]),
        cohort("evaluation_subject", 5000, 20000),
        cohort("evaluation_judge", 5000, 20000),
    ]
    role(
        "seal",
        seal_id="seal",
        fixture_id="synthetic-fixture",
        fixture_manifest_digest=binding["fixture_manifest_digest"],
        source_digest=D,
        configuration_digest=D,
        policy_id="policy",
        migration="0022",
        projector_source_version="1",
        read_algorithm_version="1",
        generation="1",
        metric_version="execution-analysis-v2",
        completed_corpus_batch_id="completed-batch",
        images=[
            {
                "image_id": kind,
                "kind": "root",
                "persistence": [
                    {
                        "role": role,
                        "root_relative_path": path,
                        "filesystem_id": "rootfs",
                        "external": False,
                    }
                    for role, path in [
                        ("os", "."),
                        ("datastore", "pg"),
                        ("objects", "minio"),
                        ("redis", "redis"),
                    ]
                ],
                "sha256": D,
                "size_bytes": 1024,
                "stopped_ns": 0,
                "sealed_ns": 1,
                "format": "raw",
                "device": 1,
                "inode": i + 1,
            }
            for i, kind in enumerate(("root",))
        ],
        cohorts=cohorts,
    )
    workload = role("workload", windows=[], progress=[], source_acks=[], errors=[])
    measurements = role(
        "measurements",
        markers=[],
        live_paints=[],
        samples=[],
        sources=[],
        browsers=[],
        resources=[],
        errors=[],
    )
    resets = role("resets", resets=[])
    diagnostics = role("diagnostics", queries=[])
    dispositions = []

    def disposition(owner, kind, identity):
        state = {
            "overlay": "retained",
            "upload": "retained",
            "claim": "settled",
            "lease": "settled",
            "provider_call": "settled",
        }.get(kind, "absent")
        proof = {
            "qemu_process": "proc-starttime-absent",
            "qmp_socket": "socket-inode-absent",
            "overlay": "protected-inode-retained",
            "broker": "broker-lookup-absent",
            "upload": "object-readback",
            "claim": "claim-readback",
            "lease": "lease-readback",
            "provider_call": "settlement-readback",
        }[kind]
        dispositions.append(
            {
                "resource_id": identity,
                "kind": kind,
                "state": state,
                "observed_ns": 10000 * SECOND,
                "owner_id": owner,
                "identity_digest": D,
                "physical_slots": 0,
                "proof_kind": proof,
            }
        )

    def link(ns, i, peer, address):
        return {
            "namespace_inode": ns,
            "ifindex": i,
            "peer_ifindex": peer,
            "address": address,
            "route_destination": "192.0.2.0/30",
            "route_ifindex": i,
            "qdisc_kind": "netem",
            "delay_us": 25000,
            "rate_bps": 20000000,
            "default_route": False,
        }

    network = role(
        "network",
        backend="owned-netns-veth-qemu-usernet",
        host_link=link(1, 1, 2, "192.0.2.1"),
        client_link=link(2, 2, 1, "192.0.2.2"),
        forward_bound_address="192.0.2.1",
        guest_ports=[8000],
        global_ip_forward_changed=False,
        nat_changed=False,
        cdp_delay_ms=0,
        context_ids=[],
        calibrations=[],
        phase_intervals=[],
        validity={
            "added_rtt_min_ns": 49_000_000,
            "added_rtt_max_ns": 51_000_000,
            "throughput_min_bps": 19_000_000,
            "throughput_max_bps": 21_000_000,
        },
    )
    for w in range(101):
        wid = f"window-{w}"
        boot = f"boot-{w}"
        start = (w * 50 + 20) * SECOND
        seconds = 30
        guest_start = 20 * SECOND
        guest_end = guest_start + seconds * SECOND
        contexts = [f"context-{w}-{i}" for i in range(10)]
        sessions = [f"session-{w}-{i}" for i in range(10)]
        protocol["windows"].append(
            {
                "window_id": wid,
                "startup_ns": 10 * SECOND,
                "marker_count": (seconds + 10) * 2 + 1,
                "marker_anchor": "first-minimal-ready-receipt",
                "trigger": "guest-open-receipt",
                "seconds": seconds,
                "calibration_window": {"offset_ns": 0, "budget_ns": 15 * SECOND},
                "measurement": {
                    "start_offset_ns": 0,
                    "end_offset_ns": 27 * SECOND,
                    "control_margin_ns": SECOND,
                },
                "session_ids": sessions,
            }
        )
        claims = [
            {
                "run_id": f"run-{w}-{i}",
                "activity_id": f"activity-{w}-{i}",
                "generation": 1,
                "claim_generation": 1,
                "call_identity": f"call-{w}-{i}",
                "session_id": sessions[i],
                "policy_id": "policy",
                "configured_model": "acceptance-live",
                "stream": True,
                "boot_id": boot,
            }
            for i in range(10)
        ]
        ticks = [
            {
                "before_ns": t,
                "after_ns": t,
                "status": "running",
                "subject_concurrency": 5,
                "judge_concurrency": 2,
                "environment_concurrency": 5,
                "sends": idx + 1,
                "settled": idx,
                "active_call_ids": [f"batch-call-{w}-{idx}"],
            }
            for idx, t in enumerate(range(18 * SECOND, guest_end + 1, 100000000))
        ]
        workload["windows"].append(
            {
                "window_id": wid,
                "boot_id": boot,
                "guest_minimal_ready_ns": 10 * SECOND,
                "guest_start_ns": guest_start,
                "guest_cohort_ns": 18 * SECOND,
                "guest_ready_ns": 19 * SECOND,
                "guest_done_ns": guest_end,
                "guest_measurement_closed_ns": guest_start + 27 * SECOND,
                "host_measurement_closed_received_ns": start + 27 * SECOND,
                "host_metadata_received_ns": start - 10 * SECOND,
                "host_cohort_received_ns": start - 3 * SECOND,
                "host_ready_sent_ns": start - 2 * SECOND,
                "host_done_sent_ns": start + seconds * SECOND,
                "host_done_received_ns": start + seconds * SECOND + 1000,
                "native_ready_digest": D,
                "native_done_digest": D,
                "snapshots": [
                    {
                        "before_ns": tick["before_ns"],
                        "after_ns": tick["after_ns"],
                        "boot_id": boot,
                        "claims": [
                            {
                                **{
                                    k: c[k]
                                    for k in (
                                        "run_id",
                                        "activity_id",
                                        "generation",
                                        "claim_generation",
                                        "call_identity",
                                    )
                                },
                                "reservation_id": c["call_identity"],
                                "policy_id": "policy",
                                "claimed_by": "worker",
                                "sql_observed_at": "2026-09-18T00:00:10Z",
                                "call_started_at": "2026-09-18T00:00:01Z",
                                "heartbeat_at": "2026-09-18T00:00:09Z",
                                "claim_deadline": "2026-09-18T00:01:00Z",
                                "timeout_at": "2026-09-18T00:01:00Z",
                                "lease_live": True,
                                "settled": False,
                                "terminal": False,
                                "reservation_state": "dispatching",
                                "status": "call_started",
                                "configured_model": "acceptance-live",
                                "stream": True,
                            }
                            for c in claims
                        ],
                    }
                    for tick in ticks
                ],
                "guest_end_ns": guest_end,
                "coordinator_start_ns": start,
                "coordinator_end_ns": start + seconds * SECOND,
                "clock_id": "host-clock",
                "context_ids": contexts,
                "session_ids": sessions,
                "claims": claims,
                "batch_id": f"batch-{w}",
                "suite_version": "suite",
                "batch_results": 5000,
                "ticks": ticks,
            }
        )
        network["context_ids"].extend(contexts)
        for phase in ("baseline", "pre", "window", "post"):
            probe_start = {
                "baseline": start - 18 * SECOND,
                "pre": start - 9 * SECOND,
                "window": start,
                "post": start + seconds * SECOND,
            }[phase]
            phase_start = probe_start
            for index, action in enumerate(["echo"] * 16 + ["upload", "download"]):
                elapsed = (
                    (1_000_000 if phase == "baseline" else 51_000_000)
                    if action == "echo"
                    else 3_500_000_000
                )
                size = 32 if action == "echo" else 8 * 1024**2
                network["calibrations"].append(
                    {
                        "window_id": wid,
                        "clock_id": "host-clock",
                        "phase": phase,
                        "ordinal": index,
                        "action": action,
                        "start_ns": probe_start,
                        "end_ns": probe_start + elapsed,
                        "transport": "tcp",
                        "bytes": size,
                        "elapsed_ns": elapsed,
                        "echo_rtt_ns": elapsed if action == "echo" else None,
                        "bits_per_second": size * 8e9 / elapsed,
                        "coverage": "discrete-probe",
                    }
                )
                probe_start += elapsed
            network["phase_intervals"].append(
                {
                    "window_id": wid,
                    "clock_id": "host-clock",
                    "phase": phase,
                    "scheduled_ns": phase_start,
                    "deadline_ns": phase_start + 15 * SECOND,
                    "before_ns": phase_start,
                    "after_ns": probe_start,
                }
            )
        # One marker may conservatively bound many events. Never attach after sink call.
        for seq in range(1, (seconds + 10) * 2 + 2):
            send = start - 10 * SECOND + (seq - 1) * 500000000
            measurements["markers"].append(
                {
                    "marker_id": f"marker-{w}-{seq}",
                    "window_id": wid,
                    "boot_id": boot,
                    "clock_id": "host-clock",
                    "sequence": seq,
                    "scheduled_ns": send,
                    "sent_ns": send,
                    "completed_ns": send + 2000,
                    "installed_ack_ns": send + 1000,
                    "guest_installed_ns": guest_start - 10 * SECOND + (seq - 1) * 500000000,
                    "command_nonce": f"nonce-{w}-{seq}",
                    "response_nonce": f"nonce-{w}-{seq}",
                }
            )
        for i in range(10):
            for sequence in range(1, seconds * 2 + 1):
                event = f"event-{w}-{i}-{sequence}"
                after = guest_start + (sequence - 1) * 500000000 + 10000
                message = f"Received fragments: {sequence}"
                workload["progress"].append(
                    {
                        "progress_id": event,
                        "phase": "measured" if sequence <= 54 else "tail",
                        "window_id": wid,
                        "run_id": f"run-{w}-{i}",
                        "activity_id": f"activity-{w}-{i}",
                        "generation": 1,
                        "claim_generation": 1,
                        "boot_id": boot,
                        "pid": 1,
                        "marker_id": f"marker-{w}-{sequence + 20}",
                        "marker_sequence": sequence + 20,
                        "marker_captured_ns": after - 1000,
                        "event_id": event,
                        "sequence": sequence,
                        "before_ns": after - 1000,
                        "after_ns": after,
                        "ack": True,
                        "error": None,
                        "message": message,
                        "source_identity": event,
                        "source_activity_id": f"activity-{w}-{i}",
                        "source_generation": 1,
                        "source_claim_generation": 1,
                        "source_sequence": sequence,
                        "applied": True,
                        "observed_order": sequence,
                        "projection_revision": sequence,
                        "public_event_id": event,
                        "public_run_id": f"run-{w}-{i}",
                        "public_message": message,
                    }
                )
                send = start + (sequence - 1) * 500000000
                workload["source_acks"].append(
                    {
                        "progress_id": event,
                        "clock_id": "host-clock",
                        "received_ns": send + 2000,
                        "progress_digest": canonical_digest(workload["progress"][-1]),
                        "query_before_ns": after + 1,
                        "query_after_ns": after + 2,
                    }
                )
                measurements["live_paints"].append(
                    {
                        "progress_id": event,
                        "marker_id": f"marker-{w}-{sequence + 20}",
                        "clock_id": "host-clock",
                        "context_id": contexts[i],
                        "event_id": event,
                        "run_id": f"run-{w}-{i}",
                        "sequence": sequence,
                        "projection_revision": sequence,
                        "source_ack_received_ns": send + 2000,
                        "public_readback_received_ns": send + 4000,
                        "paint_received_ns": send + 1000000,
                        "visible": True,
                    }
                )
    cold_index = 0
    admission_index = 0
    for (dim, mode, op), count in dimensions().items():
        for ordinal in range(count):
            sid = f"{dim}-{mode}-{op}-{ordinal}"
            cold = mode == "cold"
            admission = op == "admission"
            if cold:
                cold_index += 1
            w = cold_index if cold else 0
            start = (w * 50 + 20) * SECOND + (
                0 if cold or op == "live_visible" else ordinal * 100000000
            )
            if op == "live_visible":
                start = 20 * SECOND + (ordinal % 54) * 500000000
            duration = 1000000
            if admission:
                duration = 100000000 if mode == "baseline" else 110000000
            target = {
                "scope_id": dim if dim != "admission" else "admission",
                "run_id": "probe-run" if dim == "step_capacity" else "standard-run",
                "public_id": "completed-batch" if op == "matrix" else "opaque-cut",
                "revision": "1",
                "step_id": "step" if op in ("switch", "history", "live_visible") else None,
            }
            if op == "live_visible":
                target.update(scope_id="live", run_id=f"run-0-{ordinal // 54}")
            if admission:
                target["run_id"] = f"admission-{admission_index}"
                admission_index += 1
            wid = None if mode == "baseline" else f"window-{w}"
            rid = f"reset-{w}" if cold else None
            context = f"context-{w}-{ordinal // 54 if op == 'live_visible' else 0}"
            action = f"action-{sid}"
            page = f"page-{sid}"
            protocol["samples"].append(
                {
                    "sample_id": sid,
                    "dimension": dim,
                    "mode": mode,
                    "operation": op,
                    "ordinal": ordinal,
                    "target": target,
                    "window_id": wid,
                    "physical_window_id": wid if wid is not None else "physical-" + sid,
                    "reset_id": rid,
                    "prewarm_completed_ns": 2 if mode == "warm" else None,
                    "action_id": action,
                    "page_id": page,
                    "context_id": context,
                    "live_resource_interval": {"offset_ns": SECOND, "duration_ns": 2 * SECOND}
                    if op == "live_visible"
                    else None,
                }
            )
            measurements["samples"].append(
                {
                    "sample_id": sid,
                    "clock_id": "host-clock",
                    "start_ns": start,
                    "end_ns": start + duration,
                    "status": "ok",
                    "error": None,
                    "action_id": action,
                    "trigger_ns": start,
                    "source_id": sid,
                    "browser_id": None if admission else sid,
                }
            )
            kind, completion = {
                "first_screen": ("interactive_summary", "summary_interactive"),
                "switch": ("selected_step", "selected_step_painted"),
                "history": ("history_revision", "history_painted"),
                "analysis": ("analysis_capture", "analysis_displayed"),
                "matrix": ("matrix_page", "matrix_usable"),
                "live_visible": ("progress", "progress_painted"),
                "admission": ("formal_admission", None),
            }[op]
            live = op == "live_visible"
            event = f"event-0-{ordinal // 54}-{ordinal % 54 + 1}" if live else None
            measurements["sources"].append(
                {
                    "source_id": sid,
                    "sample_id": sid,
                    "target": target,
                    "clock_id": "host-clock",
                    "submitted_ns": start + 1000,
                    "acknowledged_ns": start + 2000,
                    "readback_ns": start + 3000,
                    "kind": kind,
                    "public_event_id": event,
                    "sequence": ordinal % 54 + 1 if live else None,
                    "progress_id": event,
                    "marker_id": f"marker-0-{ordinal % 54 + 21}" if live else None,
                    "captured_runs": 100000 if op == "analysis" else 0,
                    "matrix_results": 5000 if op == "matrix" else 0,
                    "admission_session_id": f"admission-session-{ordinal}-{mode}"
                    if admission
                    else None,
                    "admission_run_id": target["run_id"] if admission else None,
                    "profile": "finite-text-120x500ms-v1" if live else "acceptance-capacity",
                    "policy_id": "policy",
                }
            )
            if not admission:
                measurements["browsers"].append(
                    {
                        "browser_id": sid,
                        "sample_id": sid,
                        "action_id": action,
                        "page_id": page,
                        "context_id": context,
                        "clock_id": "host-clock",
                        "readback_target": target,
                        "painted_target": target,
                        "public_event_id": event,
                        "sequence": ordinal % 54 + 1 if live else None,
                        "readback_observed_ns": start + 4000,
                        "paint_observed_ns": start + duration,
                        "completion": completion,
                        "visible": True,
                    }
                )
                capture_start = (20 * SECOND + SECOND // 2) if live else start
                capture_end = capture_start + (3 if live else 2) * SECOND
                measurements["resources"].append(
                    {
                        "sample_id": sid,
                        "clock_id": "host-clock",
                        "context_id": context,
                        "renderer_id": f"renderer-{w}",
                        "start_ns": capture_start,
                        "end_ns": capture_end,
                        "visible": True,
                        "forced_gc": False,
                        "scroll_start_ns": capture_start,
                        "scroll_end_ns": capture_start + SECOND,
                        "scroll_distance_px": 10000,
                        "frames": [
                            {"observed_ns": capture_start + i * 16000000, "interval_ms": 16}
                            for i in range(188 if live else 100)
                        ],
                        "heap_samples": [
                            {"observed_ns": t, "bytes": 150 * 1024**2}
                            for t in (
                                [capture_start, 21 * SECOND + 200000000, capture_end]
                                if live
                                else [capture_start + 500000000]
                            )
                        ],
                        "dom_samples": [
                            {"observed_ns": t, "rows": 150}
                            for t in (
                                [capture_start, 21 * SECOND + 200000000, capture_end]
                                if live
                                else [capture_start + 500000000]
                            )
                        ],
                        "long_tasks": [],
                    }
                )
            diagnostics["queries"].append(
                {
                    "sample_id": sid,
                    "clock_id": "host-clock",
                    "collected_ns": start + duration,
                    "clone_id": None,
                    "query_id": sid,
                    "collection": "pg_stat_statements+explain-analyze-buffers+pg_locks",
                    "scan_rows": 100,
                    "loops": 1,
                    "rows_removed": 2,
                    "shared_hit_blocks": 10,
                    "shared_read_blocks": 20,
                    "lock_wait_ns": 0,
                    "duration_ns": 1000,
                    "instrumentation_ns": 100,
                }
            )
            if cold:

                def node(
                    name, driver, child=None, backing=None, readonly=False, inode=None, image=None
                ):
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

                resets["resets"].append(
                    {
                        "reset_id": rid,
                        "sample_id": sid,
                        "window_id": wid,
                        "seal_id": "seal",
                        "process_id": w,
                        "process_start_ticks": w,
                        "process_uuid": f"process-{w}",
                        "socket_peer_pid": w,
                        "socket_device": 1,
                        "socket_inode": w + 500,
                        "executable_sha256": D,
                        "argv_digest": D,
                        "boot_id": f"boot-{w}",
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
                        "qmp_uuid": f"process-{w}",
                        "guest_cpu_count": 16,
                        "guest_memory_bytes": 32 * GIB,
                        "nodes": [
                            node("overlay", "qcow2", "overlay-file", "base"),
                            node("base", "raw", "base-file", readonly=True),
                            node("base-file", "file", readonly=True, inode=1, image="root"),
                            node("overlay-file", "file", inode=w + 100),
                        ],
                        "target_reads_before_dispatch": [],
                        "incidental_warming": ["catalog/load background writes"],
                        "helper_module": "scripts.execution_capacity.guest_main",
                        "helper_action": "cold-window",
                        "helper_nonce": f"helper-{w}",
                        "helper_response_nonce": f"helper-{w}",
                        "helper_source_digest": D,
                    }
                )
                for kind in ("qemu_process", "qmp_socket", "overlay"):
                    disposition(rid, kind, f"{rid}-{kind}")
    for kind in ("broker", "upload", "claim", "lease"):
        disposition("attempt", kind, kind)
    for window in workload["windows"]:
        window["round_origin"] = {
            "parent_attempt_id": "synthetic-attempt",
            "round_id": "round-" + window["window_id"],
            "window_id": window["window_id"],
            "sample_id": next(
                p["sample_id"] for p in protocol["samples"] if p["window_id"] == window["window_id"]
            ),
            "parent_plan_digest": D,
            "child_plan_digest": D,
            "reservation_digest": D,
            "schema_version": 1,
            "child_origin_sha256": "d" * 64,
        }
        calls = {c["call_identity"] for c in window["claims"]} | {
            c for t in window["ticks"] for c in t["active_call_ids"]
        }
        for call in sorted(calls):
            disposition(window["window_id"], "provider_call", call)
            dispositions[-1]["identity_digest"] = canonical_digest({"call_identity": call})
    for reset in resets["resets"]:
        identity = {
            "qemu_process": {
                "pid": reset["process_id"],
                "start_ticks": reset["process_start_ticks"],
                "uuid": reset["process_uuid"],
            },
            "qmp_socket": {
                "device": reset["socket_device"],
                "inode": reset["socket_inode"],
                "peer_pid": reset["socket_peer_pid"],
            },
            "overlay": {"device": 1, "inode": reset["process_id"] + 100},
        }
        for d in dispositions:
            if d["owner_id"] == reset["reset_id"]:
                d["identity_digest"] = canonical_digest(identity[d["kind"]])
    rounds = []
    base_digest = canonical_digest(cohorts)
    base_runs = {r for c in cohorts for r in c["run_ids"]}
    retained = copy.deepcopy(cohorts)
    for window in workload["windows"]:
        origin = {
            "kind": "round",
            "seal_id": "seal",
            "round": window["round_origin"],
            "boot_id": window["boot_id"],
            "clone_id": window["round_origin"]["round_id"],
        }
        additions = [cohort("live", 10, 30, ids=[c["run_id"] for c in window["claims"]])]
        admitted = [
            s["target"]["run_id"]
            for s in protocol["samples"]
            if s["window_id"] == window["window_id"] and s["operation"] == "admission"
        ]
        if admitted:
            additions.append(cohort("admission", len(admitted), 3 * len(admitted), ids=admitted))
        for c in additions:
            c["origin"] = origin
            c["cohort_id"] += "-" + window["window_id"]
        actual = base_runs | {r for c in additions for r in c["run_ids"]}
        rounds.append(
            {
                "origin": origin,
                "base_inventory_digest": base_digest,
                "cohorts": additions,
                "owned_run_count": len(actual),
                "owned_run_digest": canonical_digest(sorted(actual)),
                "errors": [],
            }
        )
        retained.extend(additions)
    for plan in protocol["samples"]:
        if plan["mode"] != "baseline":
            continue
        key = plan["physical_window_id"]
        binding_origin = {
            "parent_attempt_id": "synthetic-attempt",
            "round_id": "round-" + key,
            "sample_id": plan["sample_id"],
            "window_id": key,
            "parent_plan_digest": D,
            "child_plan_digest": D,
            "reservation_digest": D,
            "schema_version": 1,
            "child_origin_sha256": "d" * 64,
        }
        origin = {
            "kind": "round",
            "seal_id": "seal",
            "round": binding_origin,
            "boot_id": "boot-" + key,
            "clone_id": "round-" + key,
        }
        c = cohort("admission", 1, 3, ids=[plan["target"]["run_id"]])
        c.update(cohort_id="admission-" + key, origin=origin)
        owned = base_runs | set(c["run_ids"])
        rounds.append(
            {
                "origin": origin,
                "base_inventory_digest": base_digest,
                "cohorts": [c],
                "owned_run_count": len(owned),
                "owned_run_digest": canonical_digest(sorted(owned)),
                "errors": [],
            }
        )
        retained.append(c)
    total = {
        field: sum(c["source"][field] for c in retained)
        for field in ("runs", "formal_events", "observations", "visible_steps")
    }
    role(
        "cleanup",
        cohorts=retained,
        rounds=rounds,
        total=total,
        dispositions=dispositions,
        pending=[],
        quarantined=[],
        status="retained_immutable",
    )
    return json.loads(json.dumps(roles)), binding


def write_transcript(root, roles, binding):
    artifacts = []
    for name, value in roles.items():
        shard_count = (
            (len(json.dumps(value).encode()) + 16 * 1024**2 - 1) // (16 * 1024**2)
            if name in {"workload", "measurements"}
            else 1
        )
        for index in range(shard_count):
            shard = {
                k: (v[index::shard_count] if isinstance(v, list) else v) for k, v in value.items()
            }
            data = json.dumps(shard).encode()
            suffix = "" if index == 0 else f"-{index}"
            path = root / f"{name}{suffix}.json"
            path.write_bytes(data)
            artifacts.append(
                {
                    "path": path.name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "role": name,
                    "schema_version": 1 if name == "fixture" else 3,
                    "shard_index": index,
                    "shard_count": shard_count,
                }
            )
    return artifacts
