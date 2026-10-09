"""One complete synthetic load window at a time for the finite population.

This copies the old test transcript's individual window facts while assigning
each sample a unique physical window. It makes no assertion about real load.
"""

from capacity_population import SampleSlot
from scripts.acceptance.capacity_io import canonical_digest

SECOND = 10**9
DIGEST = "a" * 64


def window_bundle(slot: SampleSlot):
    if slot.loaded_window_id is None:
        raise ValueError("baseline sample has no load window")
    number = slot.ordinal
    wid = slot.loaded_window_id
    boot = f"boot-{number}"
    start = (number * 50 + 20) * SECOND
    guest_start = 20 * SECOND
    guest_end = 50 * SECOND
    contexts = [f"context-{number}-{index}" for index in range(10)]
    sessions = [f"session-{number}-{index}" for index in range(10)]
    plan = window_plan(slot)
    claims = [
        {
            "run_id": f"run-{number}-{index}",
            "activity_id": f"activity-{number}-{index}",
            "generation": 1,
            "claim_generation": 1,
            "call_identity": f"call-{number}-{index}",
            "session_id": sessions[index],
            "policy_id": "policy",
            "configured_model": "acceptance-live",
            "stream": True,
            "boot_id": boot,
        }
        for index in range(10)
    ]
    ticks = [
        {
            "before_ns": tick,
            "after_ns": tick,
            "status": "running",
            "subject_concurrency": 5,
            "judge_concurrency": 2,
            "environment_concurrency": 5,
            "sends": ordinal + 1,
            "settled": ordinal,
            "active_call_ids": [f"batch-call-{number}-{ordinal}"],
        }
        for ordinal, tick in enumerate(range(18 * SECOND, guest_end + 1, 100_000_000))
    ]
    snapshots = [
        {
            "before_ns": tick["before_ns"],
            "after_ns": tick["after_ns"],
            "boot_id": boot,
            "claims": [
                {
                    **{
                        key: claim[key]
                        for key in (
                            "run_id",
                            "activity_id",
                            "generation",
                            "claim_generation",
                            "call_identity",
                        )
                    },
                    "reservation_id": claim["call_identity"],
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
                for claim in claims
            ],
        }
        for tick in ticks
    ]
    window = {
        "round_origin": {
            "parent_attempt_id": "synthetic-attempt",
            "round_id": f"round-{wid}",
            "window_id": wid,
            "sample_id": slot.sample_id,
            "parent_plan_digest": DIGEST,
            "child_plan_digest": DIGEST,
            "reservation_digest": DIGEST,
            "schema_version": 1,
            "child_origin_sha256": "d" * 64,
        },
        "window_id": wid,
        "boot_id": boot,
        "guest_minimal_ready_ns": 10 * SECOND,
        "guest_start_ns": guest_start,
        "guest_end_ns": guest_end,
        "guest_cohort_ns": 18 * SECOND,
        "guest_ready_ns": 19 * SECOND,
        "guest_done_ns": guest_end,
        "guest_measurement_closed_ns": 47 * SECOND,
        "host_measurement_closed_received_ns": start + 27 * SECOND,
        "host_metadata_received_ns": start - 10 * SECOND,
        "host_cohort_received_ns": start - 3 * SECOND,
        "host_ready_sent_ns": start - 2 * SECOND,
        "host_done_sent_ns": start + 30 * SECOND,
        "host_done_received_ns": start + 30 * SECOND + 1000,
        "native_ready_digest": DIGEST,
        "native_done_digest": DIGEST,
        "snapshots": snapshots,
        "coordinator_start_ns": start,
        "coordinator_end_ns": start + 30 * SECOND,
        "clock_id": "host-clock",
        "context_ids": contexts,
        "session_ids": sessions,
        "claims": claims,
        "batch_id": f"batch-{number}",
        "suite_version": "suite",
        "batch_results": 5000,
        "ticks": ticks,
    }
    return plan, window


def window_plan(slot: SampleSlot):
    if slot.loaded_window_id is None:
        raise ValueError("baseline sample has no load window")
    wid = slot.loaded_window_id
    sessions = [f"session-{slot.ordinal}-{index}" for index in range(10)]
    return {
        "window_id": wid,
        "startup_ns": 10 * SECOND,
        "marker_count": 81,
        "marker_anchor": "first-minimal-ready-receipt",
        "trigger": "guest-open-receipt",
        "seconds": 30,
        "calibration_window": {"offset_ns": 0, "budget_ns": 15 * SECOND},
        "measurement": {
            "start_offset_ns": 0,
            "end_offset_ns": 27 * SECOND,
            "control_margin_ns": SECOND,
        },
        "session_ids": sessions,
    }


def progress_rows(slot: SampleSlot):
    if slot.loaded_window_id is None:
        raise ValueError("baseline sample has no load progress")
    number, wid = slot.ordinal, slot.loaded_window_id
    boot = f"boot-{number}"
    start = (number * 50 + 20) * SECOND
    for run in range(10):
        context_id = f"context-{number}-{run}"
        for sequence in range(1, 61):
            event = f"event-{number}-{run}-{sequence}"
            after = 20 * SECOND + (sequence - 1) * 500_000_000 + 10_000
            message = f"Received fragments: {sequence}"
            marker = f"marker-{number}-{sequence + 20}"
            progress = {
                "progress_id": event,
                "phase": "measured" if sequence <= 54 else "tail",
                "window_id": wid,
                "run_id": f"run-{number}-{run}",
                "activity_id": f"activity-{number}-{run}",
                "generation": 1,
                "claim_generation": 1,
                "boot_id": boot,
                "pid": 1,
                "marker_id": marker,
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
                "source_activity_id": f"activity-{number}-{run}",
                "source_generation": 1,
                "source_claim_generation": 1,
                "source_sequence": sequence,
                "applied": True,
                "observed_order": sequence,
                "projection_revision": sequence,
                "public_event_id": event,
                "public_run_id": f"run-{number}-{run}",
                "public_message": message,
            }
            received = start + (sequence - 1) * 500_000_000 + 2000
            ack = {
                "progress_id": event,
                "clock_id": "host-clock",
                "received_ns": received,
                "progress_digest": canonical_digest(progress),
                "query_before_ns": after + 1,
                "query_after_ns": after + 2,
            }
            paint = {
                "progress_id": event,
                "marker_id": marker,
                "clock_id": "host-clock",
                "context_id": context_id,
                "event_id": event,
                "run_id": progress["run_id"],
                "sequence": sequence,
                "projection_revision": sequence,
                "source_ack_received_ns": received,
                "public_readback_received_ns": received + 2000,
                "paint_received_ns": received + 998000,
                "visible": True,
            }
            yield progress, ack, paint


def marker_rows(slot: SampleSlot):
    if slot.loaded_window_id is None:
        raise ValueError("baseline sample has no load markers")
    number, wid = slot.ordinal, slot.loaded_window_id
    start = (number * 50 + 20) * SECOND
    for sequence in range(1, 82):
        sent = start - 10 * SECOND + (sequence - 1) * 500_000_000
        nonce = f"nonce-{number}-{sequence}"
        yield {
            "marker_id": f"marker-{number}-{sequence}",
            "window_id": wid,
            "boot_id": f"boot-{number}",
            "clock_id": "host-clock",
            "sequence": sequence,
            "scheduled_ns": sent,
            "sent_ns": sent,
            "completed_ns": sent + 2000,
            "installed_ack_ns": sent + 1000,
            "guest_installed_ns": 10 * SECOND + (sequence - 1) * 500_000_000,
            "command_nonce": nonce,
            "response_nonce": nonce,
        }


def network_rows(slot: SampleSlot):
    if slot.loaded_window_id is None:
        raise ValueError("baseline sample has no calibrated load network")
    start = (slot.ordinal * 50 + 20) * SECOND
    for phase in ("baseline", "pre", "window", "post"):
        phase_start = {
            "baseline": start - 18 * SECOND,
            "pre": start - 9 * SECOND,
            "window": start,
            "post": start + 30 * SECOND,
        }[phase]
        probe_start = phase_start
        for ordinal, action in enumerate(["echo"] * 16 + ["upload", "download"]):
            elapsed = (
                (1_000_000 if phase == "baseline" else 51_000_000)
                if action == "echo"
                else 3_500_000_000
            )
            size = 32 if action == "echo" else 8 * 1024**2
            yield (
                "calibration",
                {
                    "window_id": slot.loaded_window_id,
                    "clock_id": "host-clock",
                    "phase": phase,
                    "ordinal": ordinal,
                    "action": action,
                    "start_ns": probe_start,
                    "end_ns": probe_start + elapsed,
                    "transport": "tcp",
                    "bytes": size,
                    "elapsed_ns": elapsed,
                    "echo_rtt_ns": elapsed if action == "echo" else None,
                    "bits_per_second": size * 8e9 / elapsed,
                    "coverage": "discrete-probe",
                },
            )
            probe_start += elapsed
        yield (
            "phase",
            {
                "window_id": slot.loaded_window_id,
                "clock_id": "host-clock",
                "phase": phase,
                "scheduled_ns": phase_start,
                "deadline_ns": phase_start + 15 * SECOND,
                "before_ns": phase_start,
                "after_ns": probe_start,
            },
        )
