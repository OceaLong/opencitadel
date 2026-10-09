"""Fixed guest-local seal phases; invoked only by the pinned observer entrypoint."""

import asyncio
import hashlib
import json
import os
import stat
import subprocess
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from scripts.execution_capacity.attempt import AttemptLedger, digest
from scripts.execution_capacity.guest_bridge import inspect_owned, process_snapshot
from scripts.execution_capacity.host import producer_fingerprint
from scripts.execution_capacity.ownership import _open_private, _private_directory
from scripts.execution_capacity.seal_storage import acquire_paths

PG_READ = "SELECT json_build_object('system_identifier',(SELECT system_identifier::text FROM pg_control_system()),'data_directory',current_setting('data_directory'),'tablespaces',(SELECT coalesce(json_agg(json_build_object('oid',oid::text,'path',pg_tablespace_location(oid))), '[]'::json) FROM pg_tablespace WHERE pg_tablespace_location(oid)<>''))"


def run(argv, *, timeout=30):
    result = subprocess.run(
        argv,
        capture_output=True,
        check=True,
        timeout=timeout,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
    )
    if result.stderr or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("fixed seal operation output invalid")
    return result.stdout


def inspect_container(identity):
    rows = json.loads(run(["/usr/bin/docker", "inspect", "--type", "container", identity]))
    if len(rows) != 1 or rows[0]["Id"] != identity:
        raise ValueError("exact owned container absent")
    return rows[0]


def incarnation(row):
    return {
        "id": row["Id"],
        "image": row["Image"],
        "fingerprint": producer_fingerprint(row),
        "started_at": row["State"]["StartedAt"],
    }


def verify_incarnation(row, expected, *, exited=False):
    if (
        incarnation(row) != expected
        or row["HostConfig"]["Privileged"]
        or row["HostConfig"].get("RestartPolicy", {}).get("Name")
        not in {"", "no", "unless-stopped"}
    ):
        raise ValueError("owned container incarnation/restart policy differs")
    state = row["State"]
    if exited and (
        state["Running"] is not False
        or state["Status"] != "exited"
        or state["Pid"] != 0
        or state.get("Dead")
        or state.get("OOMKilled")
        or state["ExitCode"] != 0
        or state.get("FinishedAt") in {None, "", "0001-01-01T00:00:00Z"}
    ):
        raise ValueError("actual clean service exit missing")
    return row


def stop_service(role, expected, process, append):
    before = verify_incarnation(inspect_container(expected["id"]), expected)
    if not before["State"]["Running"] or process_snapshot(before["State"]["Pid"]) != process:
        raise ValueError("exact live service process changed")
    append(
        "service-stop-intent",
        {"role": role, "container": expected, "process": process, "guest_ns": time.monotonic_ns()},
    )
    # Reverify after durable intent, immediately before process control.
    before = verify_incarnation(inspect_container(expected["id"]), expected)
    if process_snapshot(before["State"]["Pid"]) != process:
        raise ValueError("service process changed before normal stop")
    signal = "SIGINT" if role == "postgres" else "SIGTERM"
    # -1 forbids Docker's timeout SIGKILL fallback. Client timeout is failure;
    # it never fabricates a service exit or grants a retry.
    run(["/usr/bin/docker", "stop", "--signal", signal, "--time", "-1", expected["id"]])
    after = inspect_container(expected["id"])
    append(
        "service-exit",
        {
            "role": role,
            "container": incarnation(after),
            "state": after["State"],
            "guest_ns": time.monotonic_ns(),
        },
    )
    verify_incarnation(after, expected, exited=True)
    return after


def parse_control(raw, expected_system_id):
    rows = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        if key in rows:
            raise ValueError("ambiguous PG control data")
        rows[key] = value
    if (
        rows.get("Database cluster state") != "shut down"
        or rows.get("Database system identifier") != expected_system_id
    ):
        raise ValueError("offline PostgreSQL cluster not clean or identity differs")
    return {
        "state": rows["Database cluster state"],
        "system_identifier": expected_system_id,
        "digest": hashlib.sha256(raw.encode()).hexdigest(),
    }


def plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise ValueError("unsupported private evidence type")


def write_private(path, value, *, budget=None):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.evidence_json import chunks

    budget = (
        budget
        if budget is not None
        else EvidenceBudget(
            bytes_limit=32 * 1024 * 1024, rows_limit=65536, row_limit=4 * 1024 * 1024
        )
    )
    parts = chunks(value, budget=budget)
    hashed, size = hashlib.sha256(), 0
    with os.fdopen(_open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb") as stream:
        for raw in parts:
            stream.write(raw)
            hashed.update(raw)
            size += len(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"sha256": hashed.hexdigest(), "size_bytes": size}


def read_private(path, *, budget=None, max_bytes=32 * 1024 * 1024):
    with os.fdopen(_open_private(path, os.O_RDONLY), "rb") as stream:
        from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

        ceiling = max_bytes
        before = os.fstat(stream.fileno())
        if before.st_size > ceiling:
            raise EvidenceQuotaError("private original document quota exceeded")
        if budget is not None:
            budget.reserve(before.st_size * 64, rows=1, largest=before.st_size)
        raw = stream.read(ceiling + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > ceiling:
            raise EvidenceQuotaError("private original document quota exceeded")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(raw) != before.st_size:
            raise ValueError("private original changed during read")
        return json.loads(raw)


def live_storage(config, rows):
    services = config["services"]
    pg = json.loads(
        run(
            [
                "/usr/bin/docker",
                "exec",
                "--user",
                "postgres",
                services["postgres"],
                "psql",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--dbname",
                config["database_name"],
                "--command",
                PG_READ,
            ]
        )
    )
    redis = json.loads(
        run(
            [
                "/usr/bin/docker",
                "exec",
                services["redis"],
                "redis-cli",
                "--json",
                "CONFIG",
                "GET",
                "dir",
                "dbfilename",
                "appendonly",
                "appenddirname",
                "save",
            ]
        )
    )
    coverage = acquire_paths(config, rows, pg, redis)
    return {"pg": pg, "redis": redis, "coverage": coverage}


def all_rows(manifest, originals=None, *, exited=False):
    rows = {}
    if originals:
        for key, expected in originals.items():
            rows[key] = verify_incarnation(inspect_container(key), expected, exited=exited)
    else:
        for expected in manifest["containers"]:
            row = inspect_owned(expected, running=None)
            rows[row["Id"]] = row

    actual_ids = set(
        run(["/usr/bin/docker", "ps", "--all", "--quiet", "--no-trunc"]).decode().split()
    )
    if actual_ids != set(rows):
        raise ValueError("guest contains an unowned or missing container")
    return rows


def phase(request, manifest, config):
    """All phases consume a fixed plan. There is no supplied success input."""
    from scripts.execution_capacity.evidence_bounds import parse_evidence_limits
    from scripts.execution_capacity.seal_build import read_export, read_tree

    evidence_limits = parse_evidence_limits(config.get("evidence_limits"))

    identity, selected = request["identity"], request["phase"]
    if set(request) != {"identity", "phase", "config_digest"} or selected not in {
        "cleanup",
        "stop",
        "offline",
    }:
        raise ValueError("fixed seal phase required")
    if (
        request["config_digest"] != digest(config)
        or config["identity"] != {k: v for k, v in identity.items() if k != "boot_id"}
        or identity["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    ):
        raise ValueError("seal config/boot/attempt identity differs")
    root = Path(config["evidence_root"])
    _private_directory(root)
    if not isinstance(config.get("protocol_id"), str) or not config["protocol_id"]:
        raise ValueError("required immutable seal protocol_id absent")
    plan = {
        "identity": identity,
        "config_digest": digest(config),
        "protocol_id": config["protocol_id"],
    }
    ledger_root = root / "operations"
    ledger = (
        AttemptLedger.create(ledger_root, plan)
        if selected == "cleanup"
        else AttemptLedger.open(ledger_root, plan)
    )
    with ledger:
        ledger.bind_clock()
        if ledger.records("phase-error") or any(
            r["body"]["phase"] == selected for r in ledger.records("phase-intent")
        ):
            raise ValueError("seal operation consumed or uncertain; private evidence retained")
        predecessors = {"cleanup": None, "stop": "cleanup", "offline": "stop"}
        previous = predecessors[selected]
        if previous and not any(
            r["body"]["phase"] == previous for r in ledger.records("phase-complete")
        ):
            raise ValueError("missing actual predecessor")
        ledger.append("phase-intent", {"phase": selected, "guest_ns": time.monotonic_ns()})
        try:
            if selected == "cleanup":
                from scripts.execution_capacity.seal_cleanup import base_cleanup

                value = asyncio.run(base_cleanup(config, ledger, manifest))
            else:
                prepared = read_private(root / "prepared.json")
                originals, processes = prepared["originals"], prepared["processes"]
                rows = all_rows(manifest, originals)
                if selected == "stop":
                    failures, exits = [], []
                    for role in ("broker", "minio", "redis", "postgres"):
                        key = config["services"][role]
                        try:
                            after = stop_service(
                                role, originals[key], processes[key], ledger.append
                            )
                            exits.append(
                                {"role": role, "state": after["State"], "container": originals[key]}
                            )
                        except BaseException as error:  # noqa: BLE001 - retain each independently owned stop failure
                            failures.append({"role": role, "type": type(error).__name__})
                    write_private(root / "store-exits.json", {"exits": exits, "errors": failures})
                    if failures:
                        raise ValueError("persistent service shutdown unresolved")
                    all_rows(manifest, originals, exited=True)
                    value = {"stores_stopped_ns": time.monotonic_ns(), "exits": exits}
                else:
                    rows = all_rows(manifest, originals, exited=True)
                    storage = prepared["storage"]
                    coverage = acquire_paths(config, rows, storage["pg"], storage["redis"])
                    if coverage != storage["coverage"]:
                        raise ValueError("persistence backing changed")
                    pgdata = next(p["path"] for p in coverage["paths"] if p["role"] == "datastore")
                    tool = Path("/usr/lib/postgresql/16/bin/pg_controldata")
                    info = tool.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != 0
                        or info.st_mode & 0o022
                        or info.st_nlink != 1
                        or hashlib.sha256(tool.read_bytes()).hexdigest()
                        != config["pg_controldata_sha256"]
                    ):
                        raise ValueError("offline PG tool differs")
                    control = parse_control(
                        run([str(tool), pgdata]).decode(), storage["pg"]["system_identifier"]
                    )
                    builds = {}
                    build_roles = prepared["build_roles"]
                    if set(build_roles) != set(rows) or not {"frontend", "api"} <= set(
                        build_roles.values()
                    ):
                        raise ValueError("complete actual build container set required")
                    for key, row in rows.items():
                        export = root / (key + ".tar")
                        fd = _open_private(export, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                        os.close(fd)
                        run(
                            ["/usr/bin/docker", "export", "--output", str(export), key],
                            timeout=config["phase_timeout_seconds"],
                        )
                        builds[key] = {
                            "image": row["Image"],
                            "configuration_digest": originals[key]["fingerprint"],
                            **read_export(export, build_roles[key]),
                        }
                        verify_incarnation(inspect_container(key), originals[key], exited=True)
                    mounts = sorted(
                        {m["Source"] for row in rows.values() for m in row["Mounts"] if not m["RW"]}
                    )
                    used = {
                        "containers": builds,
                        "source": read_tree(Path(config["source_root"])),
                        "readonly_mounts": {p: read_tree(Path(p)) for p in mounts},
                        "observer_runtime": read_tree(Path("/opt/opencitadel-capacity/observer")),
                    }
                    all_rows(manifest, originals, exited=True)
                    value = {
                        "control": control,
                        "coverage": coverage,
                        "used_build": used,
                        "used_build_digest": digest(used),
                    }
            wire_value = value
            if selected == "cleanup" and ledger.evidence_owner.journal is not None:
                from scripts.execution_capacity.cleanup_envelope import cleanup_envelope

                wire_value = cleanup_envelope(
                    value["c2c_final"], budget=ledger.evidence_owner.budget
                )
            receipt = write_private(
                root / (selected + ".json"),
                wire_value,
                budget=ledger.evidence_owner.budget if selected == "cleanup" else None,
            )
            ledger.append(
                "phase-complete", {"phase": selected, **receipt, "guest_ns": time.monotonic_ns()}
            )
            c2c_export = None
            if selected == "cleanup":
                from scripts.execution_capacity.c2c_export import snapshot_cleanup

                c2c_export = snapshot_cleanup(
                    root, ledger, value["c2c_final"], budget=ledger.evidence_owner.budget
                )
            return {
                "protocol_id": config["protocol_id"],
                "c2c_export": c2c_export,
                "identity": identity,
                "phase": selected,
                "artifact": receipt,
                "config_digest": digest(config),
                "evidence_limits": evidence_limits,
            }
        except BaseException as error:
            ledger.append(
                "phase-error",
                {
                    "phase": selected,
                    "type": type(error).__name__,
                    "guest_ns": time.monotonic_ns(),
                    "retained": True,
                },
            )
            raise
