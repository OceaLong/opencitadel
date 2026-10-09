"""Actual fixed client process over owned netns/veth/QEMU hostfwd.

Only explicit run() starts work. Records each probe's actual interval; a pre/post
pair is never reported as a continuous full-window transfer. Private evidence.
"""

import json
import math
import os
import select
import signal
import statistics
import subprocess
import time
from pathlib import Path

from scripts.execution_capacity.calibration import PORT, read_key
from scripts.execution_capacity.guest_bridge import process_snapshot
from scripts.execution_capacity.reference_network import IP
from scripts.execution_capacity.reference_vm import file_identity

SCRIPT = Path("/opt/opencitadel-capacity/calibration.py")


def assess(result, baseline, rule):
    keys = {"added_rtt_min_ns", "added_rtt_max_ns", "throughput_min_bps", "throughput_max_bps"}
    if (
        set(rule) != keys
        or any(type(v) is not int or v <= 0 for v in rule.values())
        or rule["added_rtt_min_ns"] > rule["added_rtt_max_ns"]
        or rule["throughput_min_bps"] > rule["throughput_max_bps"]
    ):
        raise ValueError("explicit preregistered calibration validity bounds required")
    expected = ["echo"] * 16 + ["upload", "download"]
    if (
        result["errors"]
        or baseline["errors"]
        or result["attempted"] != 18
        or result["expected"] != 18
        or [r["action"] for r in result["rows"]] != expected
    ):
        raise ValueError("incomplete calibration observations")
    echoes = [r["echo_rtt_ns"] for r in result["rows"] if r["action"] == "echo"]
    native = [r["echo_rtt_ns"] for r in baseline["rows"] if r["action"] == "echo"]
    rates = [r["bits_per_second"] for r in result["rows"] if r["action"] != "echo"]
    if len(native) != 16 or any(not math.isfinite(v) or v <= 0 for v in echoes + native + rates):
        raise ValueError("incomplete/nonfinite actual calibration measurements")
    added = statistics.median(echoes) - statistics.median(native)
    valid = rule["added_rtt_min_ns"] <= added <= rule["added_rtt_max_ns"] and all(
        rule["throughput_min_bps"] <= rate <= rule["throughput_max_bps"] for rate in rates
    )
    return {
        "observed_added_median_rtt_ns": added,
        "native_median_rtt_ns": statistics.median(native),
        "shaped_median_rtt_ns": statistics.median(echoes),
        "valid": valid,
        "validity_rule": rule,
        "coverage": "discrete-probes-only",
    }


def ready_line(process, *, deadline_ns=None):
    deadline = time.monotonic() + 5
    if deadline_ns is not None:
        deadline = min(deadline, deadline_ns / 1e9)
    raw = bytearray()
    while len(raw) < 4096:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
            raise TimeoutError("calibration child readiness deadline")
        value = os.read(process.stdout.fileno(), 1)
        if not value:
            raise EOFError("calibration child exited before registration")
        raw.extend(value)
        if value == b"\n":
            return json.loads(raw)
    raise ValueError("calibration child readiness exceeds bound")


class Calibration:
    def __init__(self, vm, session, network, resources):
        if not (vm.ledger is session.ledger is network.ledger is resources.ledger):
            raise ValueError("one immutable attempt authority required")
        self.vm, self.session, self.network, self.resources = vm, session, network, resources
        self.ledger = vm.ledger
        self.config = self.ledger.plan["calibration"]
        if not any(
            r["body"]["uuid"] == vm.plan.uuid for r in self.ledger.records("resource-vm-bound")
        ):
            raise ValueError("actual VM resource/network binding required before calibration")
        if PORT not in vm.plan.ports or vm.plan.host_address != network.plan.host_address:
            raise ValueError("fixed calibration endpoint must use the same VM hostfwd/veth path")
        if session.identity["attempt_id"] != network.plan.attempt_id:
            raise ValueError("calibration attempt differs")

    def _records(self, kind):
        return [
            r["body"]
            for r in self.ledger.records(kind)
            if r["body"].get("identity") == self.session.identity
        ]

    def run(self, phase, *, deadline_ns=None):
        def remaining():
            if deadline_ns is None:
                return 120.0
            budget = (deadline_ns - time.monotonic_ns()) / 1e9
            if budget <= 0:
                raise TimeoutError("calibration fixed phase deadline exhausted")
            return min(120.0, budget)

        if phase not in {"baseline", "pre", "window", "post"}:
            raise ValueError("fixed calibration phase required")
        with self.ledger.control_lock:
            remaining()  # Includes dispatch and control-lock waiting.
            if phase not in self.config["phases"]:
                raise ValueError("phase absent from preregistration")
            if any(r["phase"] == phase for r in self._records("calibration-intent")):
                raise ValueError("calibration already consumed; no retry")
            if file_identity(SCRIPT)["sha256"] != self.config["service_sha256"]:
                raise ValueError("host calibration script differs")
            python = Path("/usr/bin/python3").resolve(strict=True)
            if file_identity(python)["sha256"] != self.config["host_python_sha256"]:
                raise ValueError("host calibration Python differs")
            observed_service = self.session.infrastructure("observe")["calibration"]
            before = self.network.observe(shaped=phase != "baseline")
            if phase == "baseline":
                if any(
                    r["operation"].endswith("-shape")
                    for r in self.network._rows("network-effect-intent")
                ):
                    raise ValueError("native baseline must precede shaping")
            else:
                baselines = [
                    r for r in self._records("calibration-result") if r["phase"] == "baseline"
                ]
                if len(baselines) != 1:
                    raise ValueError("same-boot native baseline required")
            key = read_key(Path(self.config["host_key_path"]))
            command = [
                IP,
                "netns",
                "exec",
                self.network.plan.namespace,
                "/usr/bin/python3",
                "-I",
                str(SCRIPT),
                "client",
            ]
            consumer_id = "calibration:" + self.session.identity["window_id"] + ":" + phase
            remaining()
            self.network.reserve_consumer(consumer_id, client=True)
            self.ledger.append(
                "calibration-intent",
                {
                    "identity": self.session.identity,
                    "phase": phase,
                    "argv": command,
                    "host_ns": time.monotonic_ns(),
                    "service": observed_service,
                },
            )
        process, pidfd = None, None
        try:
            remaining()
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            pidfd = os.pidfd_open(process.pid)
            if ready_line(process, deadline_ns=deadline_ns) != {"ready_pid": process.pid}:
                raise ValueError("calibration child PID differs")
            child = process_snapshot(process.pid)
            argv = (
                b"\0".join(v.encode() for v in ["/usr/bin/python3", "-I", str(SCRIPT), "client"])
                + b"\0"
            )
            from hashlib import sha256

            if (
                child["executable_sha256"] != self.config["host_python_sha256"]
                or child["argv_digest"] != sha256(argv).hexdigest()
            ):
                raise ValueError("calibration child actual executable/argv differs")
            allocated = self.resources.bind("client", process.pid, child)
            self.network.register_consumer(
                process.pid, allocated["identity"], client=True, consumer_id=consumer_id
            )
            payload = {
                "host_address": self.network.plan.host_address,
                "client_address": self.network.plan.client_address,
                "key_hex": key.hex(),
                "server": observed_service["server"],
            }
            # Secret travels only over this owned pipe. Never journal payload.
            output, _ = process.communicate(
                json.dumps(payload).encode() + b"\n", timeout=remaining()
            )
            if len(output) > 65536:
                raise ValueError("calibration child result bound")
            result = json.loads(output)
            self.ledger.append(
                "calibration-client-exited",
                {
                    "identity": allocated["identity"],
                    "returncode": process.returncode,
                    "phase": phase,
                    "measurements": result,
                    "host_ns": time.monotonic_ns(),
                },
            )
            after = self.network.observe(shaped=phase != "baseline")
            end_service = self.session.infrastructure("observe")["calibration"]
            if end_service["process"] != observed_service["process"]:
                raise ValueError("calibration service changed during probes")
            observation = {
                "identity": self.session.identity,
                "phase": phase,
                "measurements": result,
                "network_before": before,
                "network_after": after,
                "service_before": observed_service,
                "service_after": end_service,
                "host_ns": time.monotonic_ns(),
                "coverage": "discrete-probes-only",
            }
            if phase != "baseline":
                observation["validity"] = assess(
                    result, baselines[0]["measurements"], self.config["validity"]
                )
                observation["qdisc_deltas"] = {}
                for direction in ("host", "client"):
                    key_name = direction + "_qdisc"
                    delta = {
                        key: after[key_name][key] - before[key_name][key]
                        for key in ("bytes", "packets", "drops")
                    }
                    if min(delta.values()) < 0:
                        raise ValueError("qdisc counters reset during calibration")
                    observation["qdisc_deltas"][direction] = delta
            self.ledger.append("calibration-result", observation)
            remaining()
            if process.returncode != 0 or result["errors"] or len(result["rows"]) != 18:
                raise ValueError("calibration failed; observations retained")
            if phase != "baseline" and not observation["validity"]["valid"]:
                raise ValueError("calibration outside preregistered validity bounds")
            return observation
        except BaseException as exc:
            disposition = "unknown-retained"
            # pidfd targets only this spawned child, never a numeric reused PID.
            if pidfd is not None and process.poll() is None:
                try:
                    signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                    process.wait(timeout=3)
                    disposition = "client-terminated-failure-retained"
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self.ledger.append(
                "calibration-error",
                {
                    "identity": self.session.identity,
                    "phase": phase,
                    "error": type(exc).__name__,
                    "disposition": disposition,
                    "host_ns": time.monotonic_ns(),
                },
            )
            raise
        finally:
            if pidfd is not None:
                os.close(pidfd)
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()


def export_probes(observation, *, clock_id):
    """Allowlisted shared records for each actual probe; no invented spanning interval."""
    from scripts.acceptance.capacity_models import Calibration as Probe

    result = observation["measurements"]
    if (
        result["errors"]
        or result["attempted"] != 18
        or result["expected"] != 18
        or [r["action"] for r in result["rows"]] != ["echo"] * 16 + ["upload", "download"]
    ):
        raise ValueError("incomplete calibration observations")
    return [
        Probe.model_validate(
            {
                "window_id": observation["identity"]["window_id"],
                "clock_id": clock_id,
                "phase": observation["phase"],
                "ordinal": i,
                "transport": "tcp",
                "coverage": "discrete-probe",
                **{
                    key: row[key]
                    for key in (
                        "action",
                        "bytes",
                        "elapsed_ns",
                        "bits_per_second",
                        "start_ns",
                        "end_ns",
                        "echo_rtt_ns",
                    )
                },
            }
        )
        for i, row in enumerate(result["rows"])
    ]
