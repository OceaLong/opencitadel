"""Host readback for exact journaled leases; never prefix deletion or repair."""

import hashlib
import json
import time
from dataclasses import dataclass, field

from scripts.execution_capacity.broker_inventory import expected_requests, read_broker, reconcile


@dataclass(frozen=True)
class PhysicalJournalSnapshot:
    """Owner-thread copy of all evidence consumed by the physical verifier.

    Serialized immutable rows hold no SQLite connections or mutable aliases.
    Capture occurs after writer exit and final lease observation, before Docker
    work is offloaded. Readers receive fresh decoded values on every traversal.
    """

    rows: tuple
    budget: object = field(default=None, repr=False, compare=False)
    owner: object = field(default=None, repr=False, compare=False)

    @classmethod
    def capture(cls, journal):
        from scripts.execution_capacity.evidence_bounds import EvidenceBudget

        budget = getattr(journal, "budget", None) or EvidenceBudget()

        from scripts.execution_capacity.final_inventory import CumulativeJournal

        if (
            type(journal) is CumulativeJournal
            and journal.evidence is not None
            and journal.evidence.journal is not None
        ):
            owner = journal.evidence.journal
            rows = tuple(
                (kind, journal.records(kind))
                for kind in ("lease", "environment_observation", "broker_request")
            )
            return cls(rows, budget, owner)

        def encoded(row):
            budget.charge(row)
            return json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)

        return cls(
            tuple(
                (
                    kind,
                    tuple(
                        (
                            key,
                            encoded(row),
                        )
                        for key, row in journal.records(kind)
                    ),
                )
                for kind in ("lease", "environment_observation", "broker_request")
            ),
            budget,
        )

    def records(self, kind):
        for name, rows in self.rows:
            if name == kind:
                if self.owner is not None:
                    self.owner._usable()
                    self.owner._collection_metadata(rows)
                    return rows
                values = []
                for key, body in rows:
                    if self.budget is not None:
                        self.budget.reserve(len(body) * 64, rows=1)
                    values.append((key, json.loads(body)))
                return values
        raise ValueError("physical evidence kind absent from snapshot")


def verify_physical_clean(binding, journal, docker, *, observations, broker_inventory=None):
    budget = getattr(journal, "budget", None)
    actual = (
        broker_inventory
        if broker_inventory is not None
        else read_broker(binding, docker, budget=budget)
    )
    leases = physical_parents(journal, actual)
    retained = observations
    for identity, lease in leases.items():
        for kind in ("container", "network"):
            args = namespace_request(kind, lease["body"]["namespace"])
            row = {
                "lease_id": identity,
                "kind": kind,
                "request": args,
                "start_ns": time.monotonic_ns(),
                "response": None,
                "error": None,
            }
            if budget is not None:
                budget.reserve(4096, rows=1)
            retained.append(row)
            try:
                raw = docker(*args)
                if len(raw) > 1024 * 1024:
                    raise ValueError("physical namespace response quota exceeded")
                if budget is not None:
                    budget.reserve(len(raw) * 8, rows=1)
                row["response"] = raw.decode("utf-8", errors="strict")
            except Exception as error:
                row["error"] = type(error).__name__
                raise
            finally:
                row["end_ns"] = time.monotonic_ns()
    return replay_physical(journal, actual, retained)


def namespace_request(kind, namespace):
    args = [
        kind,
        "ls",
        "-q",
        "--no-trunc",
        "--filter",
        "label=opencitadel.e04.namespace=" + namespace,
    ]
    if kind == "container":
        args.insert(2, "-a")
    return args


def physical_parents(journal, actual):
    leases = dict(journal.records("lease"))
    if not leases or any(row["receipt"] is None for row in leases.values()):
        raise ValueError("exact verified-clean lease receipts missing")
    expected = {}
    for _, record in journal.records("environment_observation"):
        row = record["body"]
        if row["lease_id"] not in leases:
            raise ValueError("foreign environment operation")
        if (
            row["status"] == "done"
            and row["error"] is None
            and row["claim_until"] is None
            and row["receipt"]
        ):
            lease = leases[row["lease_id"]]["body"]
            key = f"{lease['scope']}:{row['lease_id']}:{row['generation']}:{row['id']}:{row['claim_generation']}"
            digest = hashlib.sha256(
                json.dumps(row["receipt"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if key in expected and expected[key] != digest:
                raise ValueError("operation receipt changed")
            expected[key] = digest
    originals = expected_requests(journal)
    if (
        reconcile(actual, originals)
        or set(expected) != set(originals.operations)
        or any(
            originals.operations[key]["result_sha256"] != value for key, value in expected.items()
        )
    ):
        raise ValueError("broker pending/unknown/foreign operation; restoration withheld")
    return leases


def replay_physical(journal, actual, observations):
    """Replay original exact requests/responses; never infer absence from a zero."""
    leases = physical_parents(journal, actual)
    expected = [(identity, kind) for identity in leases for kind in ("container", "network")]
    if [(r["lease_id"], r["kind"]) for r in observations] != expected:
        raise ValueError("physical observation coverage differs")
    for row in observations:
        lease = leases[row["lease_id"]]
        if (
            row["request"] != namespace_request(row["kind"], lease["body"]["namespace"])
            or row["error"] is not None
            or not isinstance(row["response"], str)
            or not 0 < row["start_ns"] <= row["end_ns"]
        ):
            raise ValueError("original physical observation differs")
        if row["response"].strip():
            raise ValueError("owned lease still has physical resources; restoration withheld")
    return {
        "broker_operations": len(actual.operations),
        "broker_bindings": len(actual.bindings),
        "broker_pages": len(actual.pages),
        "verified_clean_leases": len(leases),
        "retained_resources": 0,
        "transient_bootstrap": "broker_attested_not_independently_host_observed",
    }


def verify_active_leases(binding, journal, docker):
    """Read exact lease namespaces on their separate cell/target networks.

    Creation/cleanup can race this observer. Missing resources are not certified
    clean here; final quiescent absence uses verify_physical_clean instead.
    """
    from uuid import NAMESPACE_URL, uuid5

    from app.domain.evaluation.configuration import digest

    states = {}
    for _, observation in journal.records("lease_state"):
        row = observation["body"]
        previous = states.get(row["lease_id"])
        if previous is None or previous["revision"] < row["revision"]:
            states[row["lease_id"]] = row
    for identity, record in journal.records("lease"):
        if record["receipt"] is not None:
            continue
        lease = record["body"]
        namespace = lease["namespace"]
        expected_labels = {
            "opencitadel.e04.acceptance.project": binding["project"],
            "opencitadel.e04.acceptance.run": binding["invocation"],
            "opencitadel.e04.lease": identity,
            "opencitadel.e04.generation": str(lease["generation"]),
            "opencitadel.e04.version": lease["environment_version"],
            "opencitadel.e04.scope": digest(lease["scope"]),
            "opencitadel.e04.namespace": namespace,
        }
        networks = {}
        for kind in ("network", "container"):
            args = [
                kind,
                "ls",
                "-q",
                "--no-trunc",
                "--filter",
                "label=opencitadel.e04.namespace=" + namespace,
            ]
            if kind == "container":
                args.insert(2, "-a")
            for resource_id in docker(*args).decode().split():
                # A raced disappearance is conservatively a retained failure;
                # no retry or timeout can manufacture successful readback.
                actual = json.loads(docker(kind, "inspect", resource_id))[0]
                labels = actual["Labels"] if kind == "network" else actual["Config"]["Labels"]
                role = labels.get("opencitadel.e04.role")
                expected = {
                    **expected_labels,
                    "opencitadel.e04.role": role,
                    "opencitadel.e04.operation": str(
                        uuid5(NAMESPACE_URL, f"{identity}:{lease['generation']}:{role}")
                    ),
                }
                name = actual["Name"].removeprefix("/")
                if (
                    actual["Id"] != resource_id
                    or name != namespace + "-" + str(role)
                    or any(labels.get(k) != v for k, v in expected.items())
                ):
                    raise ValueError("physical resource exact lease labels differ")
                if kind == "network":
                    if (
                        role not in {"cell-network", "target-network"}
                        or not actual["Internal"]
                        or actual["Driver"] != "bridge"
                        or actual.get("EnableIPv6")
                        or actual.get("Options", {}).get(
                            "com.docker.network.bridge.gateway_mode_ipv4"
                        )
                        != "isolated"
                    ):
                        raise ValueError("environment network containment differs")
                    networks[role] = resource_id
                    continue
                if role not in {"case", "allowed", "denied", "proxy", "bootstrap"}:
                    raise ValueError("unknown environment resource role")
                host = actual["HostConfig"]
                image = binding["broker"][
                    "bootstrap_image_id"
                    if role == "bootstrap"
                    else "case_image_id"
                    if role == "case"
                    else "fixture_image_id"
                ]
                caps = [c.removeprefix("CAP_") for c in (host.get("CapAdd") or [])]
                limits = binding["batch"]["environment"]["limits"]
                if (
                    actual["Config"]["User"] != ("0:0" if role == "bootstrap" else "1000:1000")
                    or host["Memory"] != limits["memory_mb"] * 1024 * 1024
                    or host["NanoCpus"] != limits["cpu_millis"] * 1_000_000
                    or host["PidsLimit"] != limits["pids"]
                    or actual["Image"] != image
                    or host["Privileged"]
                    or host.get("Devices")
                    or actual.get("Mounts")
                    or not host["ReadonlyRootfs"]
                    or caps != (["NET_ADMIN"] if role == "bootstrap" else [])
                    or "ALL" not in (host.get("CapDrop") or [])
                    or not {"no-new-privileges", "no-new-privileges:true"}.intersection(
                        host.get("SecurityOpt") or []
                    )
                    or any(actual["NetworkSettings"]["Ports"].values())
                ):
                    raise ValueError("environment container image/config containment differs")
                actual_networks = {
                    n["NetworkID"] for n in actual["NetworkSettings"]["Networks"].values()
                }
                allowed = (
                    {networks.get("cell-network")}
                    if role == "case"
                    else {networks.get("target-network")}
                    if role in {"allowed", "denied"}
                    else set(networks.values())
                )
                if role == "bootstrap":
                    case = json.loads(docker("container", "inspect", namespace + "-case"))[0]
                    case_labels = case["Config"]["Labels"]
                    if (
                        any(case_labels.get(k) != v for k, v in expected_labels.items())
                        or case_labels.get("opencitadel.e04.role") != "case"
                        or host["NetworkMode"] != "container:" + case["Id"]
                    ):
                        raise ValueError("bootstrap network namespace differs")
                elif (
                    not actual_networks
                    or not actual_networks <= allowed
                    or binding["network_id"] in actual_networks
                    or (
                        role == "proxy"
                        and states.get(identity, {}).get("state") in {"ready", "leased"}
                        and actual_networks != set(networks.values())
                    )
                ):
                    raise ValueError("environment network memberships differ")
