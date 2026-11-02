"""Full ledger cardinality at the private/public join, not full proof replay.

The fixture constructs only strict bounded plan/origin records. It does not
stand in for the mandatory source/facade positive or original semantic replay.
"""

import pytest
from scripts.acceptance.capacity_models import Plan, Protocol, RoundOrigin


def coverage():
    groups = [
        (
            "standard",
            "warm",
            ["first_screen", "switch", "history", "analysis", "matrix", "live_visible"],
            100,
        ),
        ("standard", "cold", ["first_screen", "history", "analysis"], 20),
        ("step_capacity", "warm", ["first_screen", "switch", "history"], 100),
        ("step_capacity", "cold", ["first_screen", "history"], 20),
        ("admission", "baseline", ["admission"], 100),
        ("admission", "loaded", ["admission"], 100),
    ]
    plans, origins = [], []
    for dimension, mode, operations, count in groups:
        for operation in operations:
            for ordinal in range(count):
                key = str(len(plans))
                plans.append(
                    Plan(
                        sample_id="sample-" + key,
                        dimension=dimension,
                        mode=mode,
                        operation=operation,
                        ordinal=ordinal,
                        target={
                            "scope_id": "scope",
                            "run_id": "run-" + key,
                            "public_id": "public-" + key,
                            "revision": "1",
                            "step_id": None,
                        },
                        window_id=None if mode == "baseline" else "physical-" + key,
                        physical_window_id="physical-" + key,
                        reset_id="reset-" + key if mode == "cold" else None,
                        prewarm_completed_ns=2 if mode == "warm" else None,
                        action_id="action-" + key,
                        page_id="page-" + key,
                        context_id="context-" + key,
                        live_resource_interval={"offset_ns": 1, "duration_ns": 1000000000}
                        if operation == "live_visible"
                        else None,
                    )
                )
                origins.append(
                    RoundOrigin(
                        schema_version=1,
                        parent_attempt_id="attempt",
                        round_id="round-" + key,
                        sample_id="sample-" + key,
                        window_id="physical-" + key,
                        parent_plan_digest="a" * 64,
                        child_plan_digest="b" * 64,
                        reservation_digest="c" * 64,
                        child_origin_sha256="d" * 64,
                    )
                )
    protocol = Protocol(
        attempt_id="attempt",
        protocol_id="protocol",
        binding_digest="a" * 64,
        seal_id="seal",
        registered_ns=1,
        clock_id="clock",
        clock_unit="nanoseconds",
        latency_method="coordinator_predispatch_to_paint_upper_bound",
        rate_rule="each-1s-bin-at-least-2-v1",
        startup_ns=1,
        marker_cadence_ns=1,
        marker_timeout_ns=1,
        marker_count_bound=1,
        marker_max_outstanding=1,
        load_ready_ns=2000000000,
        warm_plan="same-target-explicit-prewarm-before-window",
        diagnostics_timing="after-timed-read-or-separate-clone",
        backend="linux-x86_64-kvm-qemu",
        network_backend="owned-netns-veth-qemu-usernet",
        samples=plans,
        windows=[],
    )
    loaded = [
        origin for plan, origin in zip(plans, origins, strict=True) if plan.mode != "baseline"
    ]
    return protocol, origins, loaded


def test_complete_private_1200_rounds_join_only_1100_loaded_windows():
    from scripts.execution_capacity.offline_context import validate_round_origins

    protocol, origins, loaded = coverage()
    assert len(origins) == 1200
    assert len(loaded) == 1100
    assert sum(plan.window_id is None for plan in protocol.samples) == 100
    validate_round_origins(protocol, iter(origins), iter(loaded))


@pytest.mark.parametrize(
    "fault",
    [
        "missing-baseline",
        "duplicate-baseline",
        "baseline-as-loaded",
        "wrong-sample",
        "wrong-physical-window",
        "foreign-parent",
        "foreign-origin",
        "private-order",
        "loaded-order",
    ],
)
def test_private_round_join_rejects_incomplete_foreign_or_reordered_evidence(fault):
    from scripts.execution_capacity.offline_context import validate_round_origins

    protocol, origins, loaded = coverage()
    baseline = 1000
    assert protocol.samples[baseline].mode == "baseline"
    if fault == "missing-baseline":
        origins.pop(baseline)
    elif fault == "duplicate-baseline":
        origins[baseline + 1] = origins[baseline]
    elif fault == "baseline-as-loaded":
        loaded.insert(1000, origins[baseline])
    elif fault == "wrong-sample":
        origins[baseline] = origins[baseline].model_copy(update={"sample_id": "foreign"})
    elif fault == "wrong-physical-window":
        origins[baseline] = origins[baseline].model_copy(update={"window_id": "foreign"})
    elif fault == "foreign-parent":
        origins[baseline] = origins[baseline].model_copy(update={"parent_attempt_id": "foreign"})
    elif fault == "foreign-origin":
        loaded[0] = loaded[0].model_copy(update={"child_origin_sha256": "e" * 64})
    elif fault == "private-order":
        origins[baseline], origins[baseline + 1] = origins[baseline + 1], origins[baseline]
    else:
        loaded[0], loaded[1] = loaded[1], loaded[0]
    with pytest.raises(ValueError, match="round"):
        validate_round_origins(protocol, iter(origins), iter(loaded))
