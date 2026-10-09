"""Immutable full-interval feasibility checks, never hardware promises."""

from math import ceil

from pydantic import model_validator
from scripts.acceptance.capacity_models import Nat, Pos, Record


class CalibrationSchedule(Record):
    # Operator-declared conservative upper estimates, including full handshakes.
    connect_auth_ns: Pos
    echo_rtt_ns: Pos
    transfer_floor_bps: Pos
    process_control_ns: Pos
    window_offset_ns: Nat
    phase_budget_ns: Pos
    boot_timeout_ns: Pos
    source_timeout_ns: Pos

    def minimum_phase_ns(self):
        return (
            18 * self.connect_auth_ns
            + 16 * self.echo_rtt_ns
            + ceil(2 * 8 * 1024**2 * 8 * 1_000_000_000 / self.transfer_floor_bps)
            + self.process_control_ns
        )

    @model_validator(mode="after")
    def feasible(self):
        if (
            self.transfer_floor_bps > 20_000_000
            or self.echo_rtt_ns < 50_000_000
            or self.phase_budget_ns < self.minimum_phase_ns()
            or self.phase_budget_ns > 120_000_000_000
            or self.boot_timeout_ns > 300_000_000_000
            or self.source_timeout_ns > 300_000_000_000
        ):
            raise ValueError("calibration full probe schedule is infeasible")
        return self

    def check_window(self, plan):
        if (plan.calibration_window.offset_ns, plan.calibration_window.budget_ns) != (
            self.window_offset_ns,
            self.phase_budget_ns,
        ):
            raise ValueError("calibration schedule differs from immutable shared window slot")
        # Baseline and pre run before START/minimal-ready. Window phase must
        # finish before measured obligations and the unchanged protected tail.
        if self.window_offset_ns + self.phase_budget_ns > plan.measurement.end_offset_ns:
            raise ValueError("calibration phase does not fit immutable measured interval")


def validate_phases(phases):
    if phases != ["baseline", "pre", "window", "post"]:
        raise ValueError("fixed calibration phases must be preregistered in lifecycle order")
