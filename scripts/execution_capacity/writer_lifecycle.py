"""Fixed identity-checked Docker shutdown and all-writer private journal joins."""

import asyncio
import copy
import json
import time
from pathlib import Path

from scripts.execution_capacity.host import producer_fingerprint
from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal
from scripts.execution_capacity.ownership import _private_directory

PROCESS_READ = """import json,pathlib,sys
pid=int(sys.argv[1]); root=pathlib.Path('/proc')/str(pid)
stat=(root/'stat').read_text()
print(json.dumps({'pid':pid,'start_ticks':int(stat[stat.rfind(')')+2:].split()[19]),'pid_namespace':(root/'ns/pid').stat().st_ino,'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
"""


def capture_before_stop(deployment, actual, docker):
    """Seed host records actual process authority before stopping original writers."""
    from scripts.execution_capacity.attempt import digest
    from scripts.execution_capacity.guest_seal import incarnation

    with ReadOnlyRecoveryJournal(Path(deployment.binding["writer_journal_root"])) as journal:
        for key, record in journal.records("writer"):
            body = record["body"]
            matches = [
                row for row in actual.values() if row["Config"]["Hostname"] == body["hostname"]
            ]
            if len(matches) != 1 or not matches[0]["State"]["Running"]:
                raise ValueError("original writer must be captured while alive")
            row = matches[0]
            process = json.loads(
                docker("exec", row["Id"], "python", "-c", PROCESS_READ, str(body["pid"]))
            )
            if any(
                process.get(k) != body.get(k)
                for k in ("pid", "start_ticks", "pid_namespace", "boot_id")
            ):
                raise ValueError("original writer process capture differs")
            deployment.journal.intent(
                "writer_process_capture",
                key,
                {"writer_digest": digest(body), "container": incarnation(row), "process": process},
            )


def verify_prior_capture(actual, body, capture):
    from scripts.execution_capacity.attempt import digest
    from scripts.execution_capacity.guest_seal import verify_incarnation

    if (
        capture is None
        or capture["body"]["writer_digest"] != digest(body)
        or any(
            capture["body"]["process"].get(k) != body.get(k)
            for k in ("pid", "start_ticks", "pid_namespace", "boot_id")
        )
    ):
        raise ValueError("original stopped writer live capture missing or changed")
    verify_incarnation(actual, capture["body"]["container"], exited=True)


class ContainerWriters:
    """Construct only from an OwnedDeployment that has verified the actual stack.

    Services remain available. API closes first, allowing normal kernels to
    converge; provider closes after every owned producer. Docker exit readback
    (including immutable started-at and configuration) is separate from journals.
    """

    def __init__(
        self,
        deployment,
        docker,
        *,
        journal_root,
        origin=None,
        base=None,
        prior_captures=None,
        budget=None,
        inspect_transport=None,
    ):
        self.deployment, self.docker, self.root = deployment, docker, Path(journal_root)
        from scripts.execution_capacity.evidence_bounds import EvidenceBudget

        self.budget = budget if budget is not None else EvidenceBudget()
        from scripts.execution_capacity.evidence_transport import EvidenceTransport

        self.inspect_transport = (
            inspect_transport
            if inspect_transport is not None
            else EvidenceTransport(self.budget, output_limit=1024 * 1024)
        )
        if (
            type(self.inspect_transport) is not EvidenceTransport
            or self.inspect_transport.budget is not self.budget
        ):
            raise ValueError("owned bounded writer inspect transport required")
        self.exit_observations = []
        self.base, self.origin = base, origin
        if origin is not None and origin.kind == "round" and base is None:
            raise ValueError("round requires verified immutable base writer authority")
        _private_directory(self.root)
        producers, actual = deployment.verify()
        binding = deployment.binding
        expected_root = Path(binding["writer_journal_root"])
        if expected_root != self.root or self.root.resolve() != self.root:
            raise ValueError("exact private writer journal directory differs")
        ids = set(producers) | set(deployment.children) | {binding["provider_container"]}
        self.original, self.exits = {}, {}
        hostnames = {}
        for identity in sorted(ids):
            row = actual[identity]
            if row["Id"] != identity or (
                not row["State"]["Running"] and row["State"]["Status"] != "exited"
            ):
                raise ValueError("actual owned writer incarnation unavailable")
            service = binding["containers"].get(identity, {}).get("service", "capacity-seed")
            if service in {"opencitadel-api", "opencitadel-execution-kernel", "capacity-seed"}:
                mounts = [m for m in row["Mounts"] if m["Destination"] == "/capacity-writers"]
                if (
                    len(mounts) != 1
                    or mounts[0]["Type"] != "bind"
                    or mounts[0]["Source"] != str(self.root)
                    or mounts[0]["RW"] is not True
                ):
                    raise ValueError("actual writer journal mount differs")
                hostname = row["Config"]["Hostname"]
                if hostname in hostnames:
                    raise ValueError("ambiguous writer container hostname")
                hostnames[hostname] = identity
            self.budget.charge(row)
            self.original[identity] = {
                "inspection": copy.deepcopy(row),
                "fingerprint": producer_fingerprint(row),
                "started_at": row["State"]["StartedAt"],
                "service": service,
                "image": row["Image"],
            }
        self.writer_ids = {}
        self.prior_ids = set() if base is None else set(base.writers)
        with ReadOnlyRecoveryJournal(self.root, budget=self.budget) as journal:
            for key, record in journal.records("writer"):
                body = record["body"]
                if key in self.prior_ids:
                    if record != base.writers[key]:
                        raise ValueError("inherited writer history changed")
                    continue
                if (
                    origin is not None
                    and origin.kind == "round"
                    and body["boot_id"] != origin.boot_id
                ):
                    raise ValueError("new writer belongs to wrong actual boot")
                if (
                    body["invocation"] != binding["invocation"]
                    or body["source_sha256"] != binding["source_sha256"]
                    or body["hostname"] not in hostnames
                ):
                    raise ValueError("foreign writer journal process")
                container = hostnames[body["hostname"]]
                if actual[container]["State"]["Running"]:
                    if type(body.get("pid")) is not int or body["pid"] <= 0:
                        raise ValueError("actual writer process identity missing")
                    process = json.loads(
                        docker("exec", container, "python", "-c", PROCESS_READ, str(body["pid"]))
                    )
                    if any(
                        process.get(k) != body.get(k)
                        for k in ("pid", "start_ticks", "pid_namespace", "boot_id")
                    ):
                        raise ValueError("actual writer process incarnation changed")
                elif prior_captures is not None:
                    verify_prior_capture(actual[container], body, prior_captures.get(key))
                self.writer_ids[key] = container
        if set(self.writer_ids.values()) != set(hostnames.values()):
            raise ValueError("missing actual writer journal, zero writes cannot be assumed")

    def _inspect(self, identity):
        raw = self.inspect_transport("container", "inspect", identity)
        self.budget.reserve(len(raw) * 64, rows=1)
        rows = json.loads(raw)
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            raise ValueError("exact original writer inspection required")
        return rows[0]

    def _stop(self, identity):
        observation = {
            "container_id": identity,
            "start_ns": time.monotonic_ns(),
            "end_ns": None,
            "before": None,
            "after": None,
            "transports": [],
            "result": None,
            "error": None,
        }
        self.budget.reserve(2048, rows=1)
        self.exit_observations.append(observation)
        start = len(self.inspect_transport.originals)
        try:
            before = observation["before"] = self._inspect(identity)
            expected = self.original[identity]
            check_writer_before(expected, identity, before)
            if before["State"]["Running"]:
                # Preserve the existing separate 90-second stop owner/timeout.
                self.docker("stop", "--time", "90", identity)
            after = observation["after"] = self._inspect(identity)
            result = writer_exit_observation(expected, identity, before, after, time.monotonic_ns())
            self.exits[identity] = observation["result"] = result
            return result
        except BaseException as error:
            observation["error"] = type(error).__name__
            raise
        finally:
            observation["transports"] = self.inspect_transport.originals[start:]
            observation["end_ns"] = time.monotonic_ns()

    async def stop_admissions(self):
        for identity, row in self.original.items():
            if row["service"] == "opencitadel-api":
                result = await asyncio.to_thread(self._stop, identity)
                if not result["exited"]:
                    raise ValueError("API writer exit unverified")

    async def stop(self):
        # Individual failures never prevent separately-owned remaining shutdown.
        provider = self.deployment.binding["provider_container"]
        for identity in [i for i in self.original if i != provider] + [provider]:
            try:
                await asyncio.to_thread(self._stop, identity)
            except Exception as error:  # noqa: BLE001 - retain failed acquisition and continue safe cleanup
                self.exits[identity] = {
                    "container_id": identity,
                    "exited": False,
                    "error": type(error).__name__,
                }
        return list(self.exits.values())

    def final_journals(self):
        if set(self.exits) != set(self.original) or any(
            not r["exited"] for r in self.exits.values()
        ):
            raise ValueError("all original writers must actually exit before journal inventory")
        with ReadOnlyRecoveryJournal(self.root, budget=self.budget) as journal:
            writers = dict(journal.records("writer"))
            supervisors = dict(journal.records("writer_supervisor"))
            uploads = dict(journal.records("sdk_upload"))
        issues = writer_journal_issues(writers, supervisors, uploads, self.writer_ids, self.base)
        return {
            "writers": writers,
            "supervisors": supervisors,
            "uploads": uploads,
            "issues": issues,
            "writer_ids": dict(self.writer_ids),
            "original": dict(self.original),
            "exit_observations": self.exit_observations,
        }


def writer_journal_issues(writers, supervisors, uploads, writer_ids, base):
    """Single pure authority for original/inherited writer and SDK history."""
    prior_ids = set() if base is None else set(base.writers)
    issues = []
    if base is not None:
        inherited_uploads = {
            key: row for key, row in uploads.items() if row["body"]["writer_id"] in prior_ids
        }
        if inherited_uploads != base.uploads:
            issues.append({"kind": "base-upload-set", "state": "error"})
        for values, original in (
            (writers, base.writers),
            (supervisors, base.supervisors),
            (uploads, base.uploads),
        ):
            if any(values.get(key) != row for key, row in original.items()):
                issues.append({"kind": "base-history", "state": "error"})
    if set(supervisors) != set(writers):
        issues.append({"kind": "supervisor-set", "state": "error"})
    if set(writers) != set(writer_ids) | prior_ids:
        issues.append({"kind": "writer-set", "state": "error"})
    for identity, record in writers.items():
        report = supervisors.get(identity)
        if (
            record["receipt"] is None
            or record["receipt"].get("resource_closed") is not True
            or report is None
        ):
            issues.append({"kind": "writer", "identity": identity, "state": "pending"})
        elif any(
            r["state"] not in {"completed", "cancelled"} or r["error"] is not None
            for r in report["body"]["reports"]
        ):
            issues.append({"kind": "supervisor", "identity": identity, "state": "error"})
    for identity, row in uploads.items():
        if (
            row["body"]["writer_id"] not in writer_ids
            and (base is None or base.uploads.get(identity) != row)
        ) or row["receipt"] is None:
            issues.append({"kind": "sdk-upload", "identity": identity, "state": "pending"})
    return issues


def check_writer_before(expected, identity, before):
    initial = expected["inspection"]
    if (
        initial["Id"] != identity
        or expected["fingerprint"] != producer_fingerprint(initial)
        or expected["started_at"] != initial["State"]["StartedAt"]
        or expected["image"] != initial["Image"]
    ):
        raise ValueError("original writer incarnation differs")
    if (
        before["Id"] != identity
        or before["Image"] != expected["image"]
        or producer_fingerprint(before) != expected["fingerprint"]
        or before["State"]["StartedAt"] != expected["started_at"]
    ):
        raise ValueError("owned writer identity changed before stop")


def writer_exit_observation(expected, identity, before, after, observed_ns):
    check_writer_before(expected, identity, before)
    if type(after) is not dict:
        raise ValueError("original after-exit inspection missing")
    state = after["State"]
    exited = (
        after["Id"] == identity
        and after["Image"] == expected["image"]
        and producer_fingerprint(after) == expected["fingerprint"]
        and state["StartedAt"] == expected["started_at"]
        and state["Running"] is False
        and state["Status"] == "exited"
        and state["Pid"] == 0
        and not state.get("Dead", False)
        and not state.get("OOMKilled", False)
        and state["ExitCode"] == 0
        and state.get("FinishedAt") not in {None, "", "0001-01-01T00:00:00Z"}
    )
    return {
        "container_id": identity,
        "exited": exited,
        "state": state,
        "image": after["Image"],
        "fingerprint": producer_fingerprint(after),
        "observed_ns": observed_ns,
        "before": before,
        "after": after,
    }


def replay_writer_exits(writers, exits, *, budget):
    from scripts.execution_capacity.retained_final import RetainedTransport

    originals = writers["original"]
    observed = {}
    for row in writers["exit_observations"]:
        identity = row["container_id"]
        if (
            identity not in originals
            or row["error"] is not None
            or not 0 < row["start_ns"] <= row["end_ns"]
        ):
            raise ValueError("original writer exit attempt incomplete")
        transport = RetainedTransport(row["transports"], budget=budget)
        values = []
        last = row["start_ns"]
        for name in ("before", "after"):
            raw = transport("container", "inspect", identity)
            observation = transport.rows[transport.position - 1]
            if not last <= observation["start_ns"] <= observation["end_ns"] <= row["end_ns"]:
                raise ValueError("original writer inspect transport time differs")
            last = observation["end_ns"]
            budget.reserve(len(raw) * 64, rows=1)
            parsed = json.loads(raw)
            if type(parsed) is not list or len(parsed) != 1 or parsed[0] != row[name]:
                raise ValueError("original writer inspect transport differs")
            values.append(parsed[0])
        if transport.position != len(transport.rows):
            raise ValueError("extra writer inspect transport")
        result = row["result"]
        if result is None or not last <= result["observed_ns"] <= row["end_ns"]:
            raise ValueError("original writer exit observation time differs")
        replay = writer_exit_observation(
            originals[identity], identity, *values, result["observed_ns"]
        )
        if replay != result or not replay["exited"]:
            raise ValueError("original writer exit predicate rejected")
        observed[identity] = replay
    if set(observed) != set(originals) or list(observed.values()) != exits:
        raise ValueError("original writer exit set differs")
