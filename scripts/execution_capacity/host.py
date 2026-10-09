"""Fixed Docker host bridge for an explicitly provisioned private test deployment.

No arbitrary executable, shell hook, remote endpoint, image pull or shared service
control is accepted. Docker output (including Env) never reaches public reports.
"""

import hashlib
import json
import os
import re
import selectors
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from scripts.execution_capacity.observers import RecoveryJournal
from scripts.execution_capacity.ownership import (
    OwnershipJournal,
    TargetIdentity,
    _open_private,
    fixture_manifest,
)
from scripts.execution_capacity.target import verify_deployment


class HostError(RuntimeError):
    pass


def docker(*args):
    result = subprocess.run(("docker", *args), capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise HostError("owned Docker operation failed; deployment retained")
    return result.stdout


def inspect(kind, identity):
    return json.loads(docker(kind, "inspect", identity))[0]


def source_digest(root, *, budget=None):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.evidence_files import stream_file

    budget = budget if budget is not None else EvidenceBudget(bytes_limit=32 * 1024 * 1024)
    digest = hashlib.sha256()
    for folder in ("api/app", "api/core", "scripts/execution_capacity"):
        names = []
        for member in (root / folder).rglob("*.py"):
            budget.reserve(len(str(member)) * 4 + 256, rows=1)
            names.append(member)
        for path in sorted(names):
            if path.is_symlink():
                raise HostError("symlink source refused")
            name = str(path.relative_to(root)).encode() + b"\0"
            budget.charge_bytes(len(name))
            digest.update(name)
            stream_file(path, budget=budget, consumer=digest.update)
    stream_file(
        root / "scripts/seed_execution_visualization.py", budget=budget, consumer=digest.update
    )
    stream_file(
        root / "scripts/execution_capacity/compose.yml", budget=budget, consumer=digest.update
    )
    stream_file(
        root / "scripts/execution_capacity/compose.live.yml", budget=budget, consumer=digest.update
    )
    names = []
    for member in (root / "e2e/fixtures/inference-provider/lib").glob("*.mjs"):
        budget.reserve(len(str(member)) * 4 + 256, rows=1)
        names.append(member)
    for path in sorted(names):
        if path.is_symlink():
            raise HostError("symlink provider source refused")
        name = str(path.relative_to(root)).encode() + b"\0"
        budget.charge_bytes(len(name))
        digest.update(name)
        stream_file(path, budget=budget, consumer=digest.update)
    stream_file(
        root / "e2e/fixtures/inference-provider/server.mjs", budget=budget, consumer=digest.update
    )
    return digest.hexdigest()


def read_binding(path, *, budget=None):
    from scripts.execution_capacity.guest_seal import read_private

    binding = read_private(path, budget=budget)
    if binding["environment"] != "test" or binding.get("team_id") is not None:
        raise HostError("explicit personal test binding required")
    return binding


def verify_created_child(actual, expected):
    """Exact name selects a candidate; immutable configuration supplies evidence."""
    mounts = {
        row["Destination"]: [row["Type"], row["Source"], row["RW"]] for row in actual["Mounts"]
    }
    if (
        actual["Name"].removeprefix("/") != expected["name"]
        or actual["Image"] != expected["image"]
        or actual["State"]["Running"]
        or actual["State"]["Status"] != "created"
        or actual["Config"]["Entrypoint"] != ["/app/.venv/bin/python"]
        or actual["Config"]["Cmd"] != ["-m", "scripts.execution_capacity.child"]
        or actual["Config"]["User"] != expected["user"]
        or any(
            actual["Config"]["Labels"].get(key) != value
            for key, value in expected["labels"].items()
        )
        or mounts != expected["mounts"]
        or actual["HostConfig"]["Privileged"]
        or actual["HostConfig"].get("CapAdd")
        or actual["HostConfig"].get("Devices")
        or any(actual["NetworkSettings"]["Ports"].values())
        or [row["NetworkID"] for row in actual["NetworkSettings"]["Networks"].values()]
        != [expected["network"]]
        or not re.fullmatch(r"[0-9a-f]{64}", actual["Id"])
    ):
        raise HostError("exact created child configuration differs")
    return actual["Id"]


def producer_fingerprint(actual):
    return hashlib.sha256(
        json.dumps(
            {
                "config": actual["Config"],
                "mounts": actual.get("Mounts", []),
                "host": actual["HostConfig"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class OwnedDeployment:
    def __init__(self, binding, journal):
        self.binding, self.journal = binding, journal
        self.original = None
        self.child = None
        self.children = {}
        for _name, record in getattr(journal, "records", lambda _kind: [])("child"):
            if record.get("receipt") is None:
                expected = record["body"]
                if "mounts" not in expected:
                    raise HostError(
                        "incomplete child intent retained for exact manual reconciliation"
                    )
                identity = verify_created_child(inspect("container", _name), expected)
                journal.acknowledge("child", _name, {"container_id": identity})
                record = journal.get("child", _name)
            self.children[record["receipt"]["container_id"]] = record["body"]

    def verify(self, *, stopped=False):
        binding = dict(self.binding)
        binding["containers"] = dict(binding["containers"])
        for identity in {*self.children, *([self.child] if self.child else [])}:
            binding["containers"][identity] = {
                "image": self.binding["kernel_image"],
                "role": "producer",
                "service": "capacity-seed",
            }
        network = inspect("network", binding["network_id"])
        containers = {
            identity: inspect("container", identity) for identity in binding["containers"]
        }
        states = verify_deployment(binding, network, containers, live_endpoints=True)
        if stopped:
            from scripts.execution_capacity.physical import verify_active_leases

            verify_active_leases(binding, self.journal, docker)
        if stopped and any(
            running for identity, running in states.items() if identity != self.child
        ):
            raise HostError("owned producer resumed during construction")
        return {
            identity: running
            for identity, running in states.items()
            if identity not in self.children and identity != self.child
        }, containers

    def stop(self):
        current, definitions = self.verify()
        if self.binding.get("seal"):
            from scripts.execution_capacity.writer_lifecycle import capture_before_stop

            capture_before_stop(self, definitions, docker)
        for identity in current:
            self.journal.intent(
                "producer_definition",
                identity,
                {"sha256": producer_fingerprint(definitions[identity])},
            )
        recorded = self.journal.get("host", self.binding["invocation"])
        self.original = recorded["body"]["running"] if recorded else current
        if set(self.original) != set(current):
            raise HostError("original producer membership changed")
        self.journal.intent(
            "host",
            self.binding["invocation"],
            {"running": self.original, "network_id": self.binding["network_id"]},
        )
        for identity, running in self.original.items():
            if running:
                docker("stop", "--time", "45", identity)
        self.verify(stopped=True)

    def restore(self, *, converged):
        errors = []
        if self.original is None:
            return
        if not converged:
            raise HostError(
                "restoration withheld: driver termination or operational convergence unverified"
            )
        # Verify each exact immutable identity even when network contamination
        # prevented the main verification; never start foreign/replaced objects.
        for identity, running in self.original.items():
            try:
                actual = inspect("container", identity)
                expected = self.binding["containers"][identity]
                original = self.journal.get("producer_definition", identity)
                if (
                    original is not None
                    and producer_fingerprint(actual) != original["body"]["sha256"]
                ):
                    raise HostError("original producer command/configuration changed")
                if (
                    actual["Id"] != identity
                    or actual["Image"] != expected["image"]
                    or actual["Config"]["Labels"].get("com.opencitadel.acceptance.run")
                    != self.binding["invocation"]
                ):
                    raise HostError("original producer identity changed")
                if not running:
                    if actual["State"]["Running"]:
                        docker("stop", "--time", "45", identity)
                    if inspect("container", identity)["State"]["Running"]:
                        raise HostError("originally stopped producer remains running")
                    continue
                docker("start", identity)
                for _ in range(45):
                    after = inspect("container", identity)
                    if (
                        after["State"]["Running"]
                        and after["State"].get("Health", {}).get("Status", "healthy") == "healthy"
                    ):
                        break
                    time.sleep(1)
                else:
                    raise HostError("producer restoration health unverified")
            except BaseException as error:  # noqa: BLE001 - preserve cancellation and all restoration failures
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("owned producer restoration failed", errors)


def retain_physical_receipt(receipt, binding, journal, writer_journal):
    """Historical host receipt gets counts only; original readbacks stay private."""
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.evidence_transport import EvidenceTransport
    from scripts.execution_capacity.inventory_reader import ReadOnlyParents
    from scripts.execution_capacity.physical import verify_physical_clean

    owner = EvidenceOwner()
    transport = EvidenceTransport(owner.budget.child())
    observations, primary, failure_receipt = [], None, None
    try:
        summary = verify_physical_clean(
            binding,
            ReadOnlyParents((journal, writer_journal), budget=owner.budget.child()),
            transport,
            observations=observations,
        )
    except BaseException as error:
        primary = error
        from types import SimpleNamespace

        try:
            failure_receipt = owner.retain_failure(
                journal.root, error, resources=SimpleNamespace(evidence_transport=transport)
            )
        except BaseException as retention_error:  # noqa: BLE001 - retain primary with failed prefix persistence
            raise BaseExceptionGroup(
                "physical acquisition and prepaid retention failures", [error, retention_error]
            ) from None
        raise
    finally:
        try:
            import base64

            originals = []
            for frame in () if primary is not None else transport.originals:
                owner.budget.reserve((len(frame["stdout"]) + len(frame["stderr"])) * 2, rows=1)
                originals.append(
                    {
                        **{
                            key: value
                            for key, value in frame.items()
                            if key not in ("stdout", "stderr")
                        },
                        "stdout_base64": base64.b64encode(frame["stdout"]).decode("ascii"),
                        "stderr_base64": base64.b64encode(frame["stderr"]).decode("ascii"),
                    }
                )
            journal.intent(
                "physical_observation",
                str(uuid4()),
                {
                    "observations": observations,
                    "transport_originals": originals,
                    "failure_prefix": failure_receipt,
                    "error": None if primary is None else type(primary).__name__,
                },
            )
        except BaseException as retention_error:
            if primary is not None:
                raise BaseExceptionGroup(
                    "physical observation and retention failures", [primary, retention_error]
                ) from None
            raise
    receipt["physical_cleanup"] = summary


def run_host(binding_path, output, *, workspace_prefix, seed, window_end):
    binding = read_binding(binding_path)
    if binding["project"] != workspace_prefix:
        raise HostError("workspace prefix must identify the exact provisioned deployment")
    root = Path(__file__).resolve().parents[2]
    if source_digest(root) != binding["source_sha256"]:
        raise HostError("mounted build binding differs")
    target = TargetIdentity(
        "test", binding["invocation"], binding["database_name"], "user:" + binding["principal_id"]
    )
    from uuid import UUID

    manifest = fixture_manifest(
        fixture_id=UUID(binding["fixture_id"]), seed=seed, window_end=window_end, target=target
    )
    ownership = (
        OwnershipJournal.open(output, target)
        if output.exists()
        else OwnershipJournal.create(output, manifest)
    )
    with ownership, RecoveryJournal(output) as journal:
        if ownership.manifest != manifest:
            raise HostError("resume fixture binding differs")
        journal.intent("binding", binding["invocation"], binding)
        deployment = OwnedDeployment(binding, journal)
        child_process = None
        original_error = None
        granted = False
        converged = False
        attempt_id = str(uuid4())
        journal.intent(
            "attempt",
            attempt_id,
            {
                "invocation": binding["invocation"],
                "fixture_id": binding["fixture_id"],
                "source_sha256": binding["source_sha256"],
            },
        )
        try:
            _, containers = deployment.verify()
            for identity in deployment.children:
                if containers[identity]["State"]["Running"]:
                    docker("stop", "--time", "45", identity)
                if inspect("container", identity)["State"]["Running"]:
                    raise HostError("prior exact driver termination unverified")
            storage = containers[binding["minio_container"]]
            endpoint = urlsplit("//" + binding["minio_endpoint"])
            network = next(iter(storage["NetworkSettings"]["Networks"].values()))
            aliases = set(network.get("Aliases") or ()) | {storage["Name"].removeprefix("/")}
            if (
                binding["containers"][binding["minio_container"]]["service"] != "minio"
                or endpoint.hostname not in aliases
                or endpoint.username
                or endpoint.password
                or endpoint.path
                or endpoint.query
                or endpoint.fragment
                or not endpoint.port
                or f"{endpoint.port}/tcp" not in storage["Config"].get("ExposedPorts", {})
            ):
                raise HostError("selected storage is not the exact owned MinIO endpoint")
            for identity, container in containers.items():
                if identity != binding["minio_container"] and endpoint.hostname in (
                    next(iter(container["NetworkSettings"]["Networks"].values())).get("Aliases")
                    or ()
                ):
                    raise HostError("ambiguous storage network alias")
            provider = containers[binding["provider_container"]]
            provider_url = urlsplit(binding["provider_endpoint"])
            provider_net = next(iter(provider["NetworkSettings"]["Networks"].values()))
            provider_aliases = set(provider_net.get("Aliases") or ()) | {
                provider["Name"].removeprefix("/")
            }
            if (
                binding["containers"][binding["provider_container"]]["service"]
                != "inference-provider"
                or provider_url.scheme != "http"
                or provider_url.hostname not in provider_aliases
                or provider_url.username
                or provider_url.password
                or provider_url.query
                or provider_url.fragment
                or provider_url.path != "/v1"
                or not provider_url.port
                or f"{provider_url.port}/tcp" not in provider["Config"].get("ExposedPorts", {})
            ):
                raise HostError("capacity provider endpoint is not the exact owned fixture")
            for cid, item in containers.items():
                if cid != binding["provider_container"] and provider_url.hostname in (
                    next(iter(item["NetworkSettings"]["Networks"].values())).get("Aliases") or ()
                ):
                    raise HostError("ambiguous provider network alias")
            kernel = containers[binding["kernel_container"]]
            if (
                binding["containers"][binding["kernel_container"]]["service"]
                != "opencitadel-execution-kernel"
                or kernel["Image"] != binding["kernel_image"]
                or "ENV=test" not in kernel["Config"]["Env"]
            ):
                raise HostError("kernel image mismatch")
            for key in ("case_image_id", "fixture_image_id", "bootstrap_image_id"):
                image_id = binding["broker"][key]
                if (
                    not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                    or inspect("image", image_id)["Id"] != image_id
                ):
                    raise HostError("broker image prerequisite differs")
            case_image = binding["batch"]["environment"]["image_digest"]
            case_reference = (
                case_image["value"]
                if case_image["kind"] == "local_content_id"
                else case_image["repository"] + "@" + case_image["value"]
            )
            if inspect("image", case_reference)["Id"] != binding["broker"]["case_image_id"]:
                raise HostError("published environment image content differs")
            # Both original API and every kernel replica must restore the
            # dedicated genuine-storage entrypoint, never the default test stub.
            for cid, expected in binding["containers"].items():
                if expected["service"] not in {"opencitadel-api", "opencitadel-execution-kernel"}:
                    continue
                actual = containers[cid]
                command = (
                    ["-m", "scripts.execution_capacity.kernel_main"]
                    if expected["service"] == "opencitadel-execution-kernel"
                    else [
                        "-m",
                        "uvicorn",
                        "scripts.execution_capacity.api_main:create_app",
                        "--factory",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                    ]
                )
                mounted = {m["Destination"]: m for m in actual["Mounts"]}
                if (
                    actual["Config"]["Entrypoint"] != ["/app/.venv/bin/python"]
                    or actual["Config"]["Cmd"] != command
                ):
                    raise HostError("capacity original entrypoint differs")
                for destination, source in {
                    "/capacity": str(root),
                    "/capacity-binding.json": str(binding_path.resolve()),
                }.items():
                    row = mounted.get(destination)
                    if row is None or row["Type"] != "bind" or row["RW"] or row["Source"] != source:
                        raise HostError("capacity original trusted mount differs")
            from scripts.execution_capacity.ownership import _private_directory

            writer_root = Path(binding["writer_journal_root"])
            _private_directory(writer_root)
            if writer_root.resolve() != writer_root:
                raise HostError("writer journal path must be exact and non-symlink")
            for cid, expected in binding["containers"].items():
                if expected["service"] in {"opencitadel-api", "opencitadel-execution-kernel"}:
                    mounted = [
                        m
                        for m in containers[cid]["Mounts"]
                        if m["Destination"] == "/capacity-writers"
                    ]
                    if (
                        len(mounted) != 1
                        or mounted[0]["Type"] != "bind"
                        or mounted[0]["Source"] != str(writer_root)
                        or mounted[0]["RW"] is not True
                    ):
                        raise HostError("actual capacity writer journal mount differs")
            kernel_mounts = [
                m
                for m in kernel["Mounts"]
                if m["Destination"]
                not in {"/capacity", "/capacity-binding.json", "/capacity-writers"}
            ]
            # Environment copied privately from the actual selected kernel.
            env_path = output / "child.env"
            values = kernel["Config"]["Env"]
            if any("\n" in item or "\r" in item for item in values):
                raise HostError("multiline container environment unsupported")
            fd = _open_private(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            with os.fdopen(fd, "w") as stream:
                stream.write("\n".join(values) + "\nPYTHONPATH=/capacity/api:/capacity\n")
                stream.flush()
                os.fsync(stream.fileno())
            deployment.stop()
            name = "capacity-seed-" + binding["invocation"] + "-" + attempt_id
            args = [
                "create",
                "--pull",
                "never",
                "--interactive",
                "--name",
                name,
                "--network",
                binding["network_id"],
                "--env-file",
                str(env_path),
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--env",
                "CAPACITY_ATTEMPT_ID=" + attempt_id,
                "--mount",
                f"type=bind,src={root},dst=/capacity,readonly",
                "--mount",
                f"type=bind,src={output.resolve()},dst=/capacity-private",
                "--mount",
                f"type=bind,src={writer_root},dst=/capacity-writers",
                "--mount",
                f"type=bind,src={binding_path.resolve()},dst=/capacity-binding.json,readonly",
                "--entrypoint",
                "/app/.venv/bin/python",
            ]
            child_labels = {
                "com.docker.compose.project": binding["project"],
                "com.docker.compose.service": "capacity-seed",
                "com.opencitadel.acceptance.project": binding["project"],
                "com.opencitadel.acceptance.run": binding["invocation"],
                "com.opencitadel.acceptance.attempt": attempt_id,
            }
            for key, value in child_labels.items():
                args.extend(("--label", key + "=" + value))
            for mount in kernel_mounts:
                # Only operator-provisioned read-only configuration files; never
                # inherit Docker sockets, writable application data or commands.
                if (
                    mount["Destination"] not in binding["readonly_config_mounts"]
                    or mount["RW"]
                    or mount["Type"] != "bind"
                    or (
                        not re.fullmatch(
                            r"/(run|etc)/opencitadel/[A-Za-z0-9_.-]+", mount["Destination"]
                        )
                        and mount["Destination"]
                        not in {
                            "/etc/opencitadel-evaluation/environment.json",
                            "/etc/opencitadel-evaluation/budget.json",
                        }
                    )
                    or binding["readonly_config_mounts"][mount["Destination"]] != mount["Source"]
                ):
                    raise HostError("kernel config mount not explicitly safe")
                args.extend(
                    (
                        "--mount",
                        f"type=bind,src={mount['Source']},dst={mount['Destination']},readonly",
                    )
                )
            args.extend((binding["kernel_image"], "-m", "scripts.execution_capacity.child"))
            expected_child = {
                "name": name,
                "image": binding["kernel_image"],
                "network": binding["network_id"],
                "user": f"{os.getuid()}:{os.getgid()}",
                "labels": child_labels,
                "mounts": {
                    "/capacity": ["bind", str(root), False],
                    "/capacity-private": ["bind", str(output.resolve()), True],
                    "/capacity-writers": ["bind", str(writer_root), True],
                    "/capacity-binding.json": ["bind", str(binding_path.resolve()), False],
                    **{row["Destination"]: ["bind", row["Source"], False] for row in kernel_mounts},
                },
            }
            journal.intent("child", name, expected_child)
            identity = docker(*args).decode().strip()
            deployment.child = identity
            deployment.children[identity] = {
                "image": binding["kernel_image"],
                "network": binding["network_id"],
            }
            child = inspect("container", identity)
            if verify_created_child(child, expected_child) != identity:
                raise HostError("created child response identity differs")
            journal.acknowledge("child", name, {"container_id": identity})
            mounts = {row["Destination"]: row for row in child["Mounts"]}
            expected_mounts = {
                "/capacity": str(root),
                "/capacity-private": str(output.resolve()),
                "/capacity-writers": str(writer_root),
                "/capacity-binding.json": str(binding_path.resolve()),
                **{row["Destination"]: row["Source"] for row in kernel_mounts},
            }
            if (
                set(mounts) != set(expected_mounts)
                or any(
                    row["Type"] != "bind"
                    or row["Source"] != expected_mounts[destination]
                    or row["RW"] != (destination in {"/capacity-private", "/capacity-writers"})
                    for destination, row in mounts.items()
                )
                or mounts["/capacity"]["RW"]
                or mounts["/capacity-binding.json"]["RW"]
                or not mounts["/capacity-private"]["RW"]
                or child["Config"]["Entrypoint"] != ["/app/.venv/bin/python"]
                or child["Config"]["Cmd"] != ["-m", "scripts.execution_capacity.child"]
                or child["HostConfig"].get("CapAdd")
                or child["HostConfig"].get("Devices")
            ):
                raise HostError("child command, mounts or privileges differ")
            log_fd = _open_private(
                output / "child-private.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND
            )
            with os.fdopen(log_fd, "ab") as log:
                child_process = subprocess.Popen(
                    ("docker", "start", "--attach", "--interactive", identity),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                )
                with selectors.DefaultSelector() as selector:
                    selector.register(child_process.stdout, selectors.EVENT_READ)
                    last_fence = time.monotonic()
                    while True:
                        ready = selector.select(timeout=1)
                        if not ready:
                            if granted:
                                deployment.verify(stopped=True)
                            if time.monotonic() - last_fence > 300:
                                raise HostError("child control deadline exceeded")
                            continue
                        line = child_process.stdout.readline()
                        if not line:
                            break
                        if line.strip() == "capacity-seal-ready" and binding.get("seal"):
                            # QGA sealing coordinator now captures the still-live child.
                            # Continue the attach loop; never await exit before release.
                            from scripts.execution_capacity.seal_handoff import parent_ready

                            parent_ready(deployment, output, attempt_id)
                            last_fence = time.monotonic()
                            continue
                        if line.strip() != "capacity-fence":
                            raise HostError("unexpected child control output")
                        deployment.verify(stopped=True)
                        granted = True
                        child_process.stdin.write("capacity-continue\n")
                        child_process.stdin.flush()
                        last_fence = time.monotonic()
                if child_process.wait(timeout=30) != 0:
                    raise HostError("historical child failed; private recovery retained")
            actual = inspect("container", identity)
            if actual["State"]["Running"] or actual["State"]["ExitCode"] != 0:
                raise HostError("owned child exit unverified")
            receipt = json.loads((output / "historical-result.json").read_text())
            reported_convergence = (
                receipt.get("converged") is True and receipt.get("attempt_id") == attempt_id
            )
            converged = False
            if (
                not reported_convergence
                or receipt.get("status") != "corpus_ready"
                or receipt.get("fixture_complete") is not False
            ):
                raise HostError("historical completion receipt missing")
            if binding.get("seal"):
                receipt["restoration"] = "withheld_for_offline_seal"
                return receipt
            if source_digest(root) != binding["source_sha256"]:
                converged = False
                raise HostError("source changed during historical construction")
            converged = False
            with RecoveryJournal(writer_root) as writer_journal:
                retain_physical_receipt(receipt, binding, journal, writer_journal)
            converged = True
            receipt["recovery_journal_sha256"] = hashlib.sha256(
                (output / "recovery.sqlite3").read_bytes()
            ).hexdigest()
            return receipt
        except BaseException as error:
            original_error = error
            raise
        finally:
            failures = []
            if deployment.child:
                try:
                    actual = inspect("container", deployment.child)
                    if actual["State"]["Running"]:
                        docker("stop", "--time", "45", deployment.child)
                    if inspect("container", deployment.child)["State"]["Running"]:
                        raise HostError("owned driver termination unverified")
                except BaseException as error:  # noqa: BLE001 - preserve cancellation and all restoration failures
                    converged = False
                    failures.append(error)
            if child_process is not None and child_process.poll() is None:
                try:
                    child_process.terminate()
                    child_process.wait(timeout=30)
                except BaseException as error:  # noqa: BLE001 - retain primary and restoration failures
                    converged = False
                    failures.append(error)
            # If operational convergence was not proven the immutable/uncertain
            # inventory remains. Restoration errors never replace the root error.
            try:
                if not binding.get("seal"):
                    deployment.restore(converged=converged or not granted)
            except BaseException as error:  # noqa: BLE001 - preserve cancellation and all restoration failures
                failures.append(error)
            if failures:
                raise BaseExceptionGroup(
                    "seed/termination/restoration failures",
                    ([original_error] if original_error else []) + failures,
                )
            if converged and original_error is None:
                receipt["restoration"] = "exact_original_producers_restored"
                fd = _open_private(
                    output / "corpus-result.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                )
                with os.fdopen(fd, "w") as stream:
                    json.dump(receipt, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
