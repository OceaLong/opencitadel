"""Connected seal composition; only external process/client/readback effects are fake."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scripts.execution_capacity import (
    guest_bridge,
    guest_seal,
    host,
    observer_resources,
    seal_cleanup,
    seal_storage,
)
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.guest_seal import incarnation, write_private
from scripts.execution_capacity.observers import RecoveryJournal


@pytest.fixture
def owned_root(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    folders = {}
    for name in (
        "pg",
        "redis",
        "minio",
        "broker",
        "writers",
        "seed",
        "evidence",
        "source",
        "docker",
        "other",
    ):
        folders[name] = tmp_path / name
        folders[name].mkdir(mode=0o700)
    (folders["pg"] / "pg_wal").mkdir()
    (folders["pg"] / "pg_tblspc").mkdir()
    (folders["redis"] / "appendonlydir").mkdir()
    for suffix in ("", "-wal", "-shm", "-journal"):
        (folders["broker"] / ("operations.sqlite" + suffix)).write_bytes(b"private journal bytes")
    services = {
        name: str(index) * 64
        for index, name in enumerate(("postgres", "redis", "minio", "broker"), 1)
    }
    provider, child = "5" * 64, "6" * 64
    binding = {
        "environment": "test",
        "project": "capacity-owned",
        "invocation": str(uuid4()),
        "fixture_id": str(uuid4()),
        "source_sha256": "a" * 64,
        "network_id": "b" * 64,
        "database_name": "capacity",
        "minio_endpoint": "minio:9000",
        "minio_bucket": "capacity",
        "minio_container": services["minio"],
        "provider_container": provider,
        "kernel_image": "sha256:" + "c" * 64,
        "writer_journal_root": str(folders["writers"]),
        "seal": {"enabled": True},
        "containers": {},
        "principal_id": "owner",
        "probe": {"principal_id": "owner"},
    }
    labels = {
        "com.docker.compose.project": binding["project"],
        "com.opencitadel.acceptance.project": binding["project"],
        "com.opencitadel.acceptance.run": binding["invocation"],
    }
    rows = {}
    role_names = {**services, "inference-provider": provider, "capacity-seed": child}
    for index, (role, key) in enumerate(role_names.items(), 1):
        service = "opencitadel-sandbox-broker" if role == "broker" else role
        image = binding["kernel_image"]
        if key != child:
            binding["containers"][key] = {
                "service": service,
                "role": "capability-service" if role == "broker" else "storage",
                "image": image,
            }
        rows[key] = {
            "Id": key,
            "Name": "/" + role,
            "Image": image,
            "Path": "python",
            "Args": [],
            "Config": {
                "User": "1000:1000",
                "Env": [],
                "Hostname": role,
                "Labels": {**labels, "com.docker.compose.service": service},
                "Entrypoint": None,
                "Cmd": [],
                "ExposedPorts": {"9000/tcp": {}} if role == "minio" else {},
            },
            "State": {
                "Running": True,
                "Status": "running",
                "Pid": 100 + index,
                "StartedAt": "start",
                "FinishedAt": "",
                "ExitCode": 0,
                "Dead": False,
                "OOMKilled": False,
            },
            "HostConfig": {
                "NetworkMode": binding["network_id"],
                "Privileged": False,
                "RestartPolicy": {"Name": "no"},
            },
            "NetworkSettings": {
                "Ports": {},
                "Networks": {
                    "owned": {
                        "NetworkID": binding["network_id"],
                        "IPAddress": f"172.20.0.{index + 1}",
                        "Aliases": [role],
                    }
                },
            },
            "Mounts": [],
        }

    def mount(key, source, destination, *, volume=False):
        row = {
            "Source": str(source),
            "Destination": destination,
            "Type": "volume" if volume else "bind",
            "RW": True,
        }
        if volume:
            row["Name"] = "exact-owned-receipts"
        rows[key]["Mounts"].append(row)

    mount(services["postgres"], folders["pg"], "/var/lib/postgresql/data")
    mount(services["redis"], folders["redis"], "/data")
    mount(services["minio"], folders["minio"], "/data")
    rows[services["minio"]].update(Path="minio", Args=["server", "/data"])
    mount(services["broker"], folders["broker"], "/var/lib/opencitadel-evaluation", volume=True)
    mount(services["broker"], "/var/run/docker.sock", "/var/run/docker.sock")
    rows[services["broker"]]["Config"]["Cmd"] = [
        "python",
        "-m",
        "app.infrastructure.external.sandbox.broker",
    ]
    binding["broker"] = {
        "container_id": services["broker"],
        "mounts": {
            m["Destination"]: [m["Type"], m["Source"], m["RW"]]
            for m in rows[services["broker"]]["Mounts"]
        },
    }
    mount(child, folders["writers"], "/capacity-writers")
    mount(child, folders["seed"], "/capacity-output")
    mount(provider, folders["other"], "/private-provider-journals")
    binding_path = tmp_path / "binding.json"
    write_private(binding_path, binding)
    config = {
        "protocol_id": "fixture-protocol",
        "services": services,
        "writer_root": str(folders["writers"]),
        "source_root": str(folders["source"]),
        "seed_root": str(folders["seed"]),
        "evidence_root": str(folders["evidence"]),
        "binding_path": str(binding_path),
        "journal_roots": [str(folders["seed"]), str(folders["writers"])],
        "disk_serial": "root-uuid",
        "database_name": "capacity",
        "identity": {"source_digest": binding["source_sha256"]},
        "phase_timeout_seconds": 30,
        "evidence_limits": {
            "bytes_limit": 32 * 1024 * 1024,
            "rows_limit": 65_536,
            "row_limit": 4 * 1024 * 1024,
            "index_bytes": 64 * 1024,
        },
        "build_roles": dict.fromkeys(binding["containers"], "api"),
    }
    pg = {
        "data_directory": "/var/lib/postgresql/data",
        "tablespaces": [],
        "system_identifier": "123",
    }
    redis = {
        "dir": "/data",
        "dbfilename": "dump.rdb",
        "appenddirname": "appendonlydir",
        "appendonly": "yes",
    }
    observed = []

    def storage_command(argv):
        if argv[:4] == ["/usr/bin/docker", "volume", "inspect", "exact-owned-receipts"]:
            return json.dumps(
                [{"Driver": "local", "Options": None, "Mountpoint": str(folders["broker"])}]
            ).encode()
        assert argv == ["/usr/bin/docker", "info", "--format", "{{json .}}"]
        return json.dumps({"DockerRootDir": str(folders["docker"])}).encode()

    root = {
        "fstype": "ext4",
        "major_minor": "8:1",
        "source": "/dev/vda1",
        "disk": "vda",
        "serial": "root-uuid",
    }

    def mount_readback(path):
        observed.append(str(path))
        return dict(root)

    proc = tmp_path / "proc"
    for row in rows.values():
        process_root = proc / str(row["State"]["Pid"]) / "root"
        process_root.mkdir(parents=True)
        mountinfo = []
        for mount_item in row["Mounts"]:
            if mount_item["Destination"] == "/var/run/docker.sock":
                continue
            target = process_root / mount_item["Destination"].lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(mount_item["Source"])
            mountinfo.append(f"21 1 8:1 / {mount_item['Destination']} rw - ext4 /dev/vda1 rw\n")
        (process_root.parent / "mountinfo").write_text("".join(mountinfo))
    verify_namespace = seal_storage.verify_writable_namespace
    monkeypatch.setattr(
        seal_storage, "verify_writable_namespace", lambda row: verify_namespace(row, proc_root=proc)
    )
    monkeypatch.setattr(seal_storage, "command", storage_command)
    monkeypatch.setattr(seal_storage, "mount_readback", mount_readback)
    monkeypatch.setattr(
        guest_seal,
        "run",
        lambda argv, **kwargs: json.dumps(pg if "psql" in argv else redis).encode(),
    )
    return SimpleNamespace(
        config=config,
        binding=binding,
        rows=rows,
        folders=folders,
        pg=pg,
        redis=redis,
        child=child,
        provider=provider,
        observed=observed,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "broker-link",
        "sidecar-link",
        "nested-device",
        "seed-link",
        "evidence-link",
        "other-writable-link",
    ],
)
def test_actual_journal_locations_require_root_storage(owned_root, monkeypatch, mutation):
    fixture = owned_root
    broker = fixture.folders["broker"]
    if mutation.endswith("link"):
        target = {
            "broker-link": broker / "operations.sqlite",
            "sidecar-link": broker / "operations.sqlite-wal",
            "seed-link": fixture.folders["seed"] / "recovery.sqlite3",
            "evidence-link": fixture.folders["evidence"] / "recovery.sqlite3",
            "other-writable-link": fixture.folders["other"] / "nested" / "extra.sqlite",
        }[mutation]
        target.parent.mkdir(exist_ok=True)
        target.unlink(missing_ok=True)
        target.symlink_to(broker / "operations.sqlite-shm")
    elif mutation == "nested-device":
        target = broker / "nested"
        target.mkdir()
        (target / "operations.sqlite").write_bytes(b"nested external bytes")
        original = Path.stat

        def stat(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == target or path.is_relative_to(target):
                changed = list(value)
                changed[2] += 1
                return os.stat_result(changed)
            return value

        monkeypatch.setattr(Path, "stat", stat)
    if mutation == "none":
        result = seal_storage.acquire_paths(fixture.config, fixture.rows, fixture.pg, fixture.redis)
        journals = {row["path"] for row in result["paths"] if row["role"] == "broker-journal"}
        assert journals == {
            str(broker / ("operations.sqlite" + suffix))
            for suffix in ("", "-wal", "-shm", "-journal")
        }
    else:
        with pytest.raises(ValueError, match=r"symlink|nested persistence"):
            seal_storage.acquire_paths(fixture.config, fixture.rows, fixture.pg, fixture.redis)


@pytest.mark.parametrize("endpoint", ["172.20.0.4:9000", "172.20.0.99:9000"])
def test_real_base_cleanup_constructs_bound_observer(owned_root, monkeypatch, endpoint):
    from core.config import DeploymentSettings

    fixture = owned_root
    rows, binding, config = fixture.rows, fixture.binding, fixture.config
    child = fixture.child
    process = {"pid": 7, "start_ticks": 17, "pid_namespace": 27, "boot_id": str(uuid4())}
    ready = {
        "identity": {
            **process,
            "hostname": "capacity-seed",
            "source_digest": binding["source_sha256"],
            "attempt_id": str(uuid4()),
        },
        "deadline_ns": time.monotonic_ns() + 30_000_000_000,
    }
    parent = {"pid": 99, "start_ticks": 99}
    seed = fixture.folders["seed"]
    write_private(seed / "seal-ready.json", ready)
    write_private(
        seed / "seal-parent-ready.json",
        {"ready": ready, "parent": parent, "child": incarnation(rows[child])},
    )
    with RecoveryJournal(seed) as journal:
        journal.intent(
            "attempt",
            ready["identity"]["attempt_id"],
            {key: binding[key] for key in ("invocation", "fixture_id", "source_sha256")},
        )
        journal.intent("child", "original-seed", {"original": True})
        journal.acknowledge("child", "original-seed", {"container_id": child})
    with RecoveryJournal(fixture.folders["writers"]) as journal:
        journal.intent(
            "writer",
            "actual-child-writer",
            {
                **process,
                "hostname": "capacity-seed",
                "invocation": binding["invocation"],
                "source_sha256": binding["source_sha256"],
            },
        )
    config["settings"] = DeploymentSettings(
        _env_file=None,
        env="test",
        storage_provider="minio",
        postgres_host="172.20.0.2",
        postgres_db="capacity",
        minio_endpoint=endpoint,
        minio_bucket="capacity",
    ).model_dump(mode="json")
    manifest = {"containers": []}
    for key in binding["containers"]:
        row = rows[key]
        manifest["containers"].append(
            {
                "id": key,
                "image": row["Image"],
                "argv": [row["Path"], *row["Args"]],
                "user": row["Config"]["User"],
                "env_digest": hashlib.sha256(
                    guest_bridge.encoded(sorted(row["Config"]["Env"]))
                ).hexdigest(),
                "network_ids": [binding["network_id"]],
                "mounts": sorted(
                    [
                        {"source": m["Source"], "destination": m["Destination"], "rw": m["RW"]}
                        for m in row["Mounts"]
                    ],
                    key=lambda m: m["destination"],
                ),
            }
        )
    calls = []

    def docker(*args):
        if args == ("network", "inspect", binding["network_id"]):
            first = next(iter(rows.values()))
            labels = {
                k: v
                for k, v in first["Config"]["Labels"].items()
                if k != "com.docker.compose.service"
            }
            return json.dumps(
                [
                    {
                        "Id": binding["network_id"],
                        "Internal": True,
                        "Labels": labels,
                        "Containers": {
                            key: {} for key, row in rows.items() if row["State"]["Running"]
                        },
                    }
                ]
            ).encode()
        if args[:2] == ("container", "inspect"):
            return json.dumps([rows[args[2]]]).encode()
        if args[:2] == ("inspect", "--type"):
            return json.dumps([rows[args[3]]]).encode()
        if args[:1] == ("exec",):
            assert args[1] == child
            return json.dumps(process).encode()
        if args == ("ps", "--all", "--quiet", "--no-trunc"):
            return "\n".join(rows).encode()
        if args[:1] == ("stop",):
            rows[args[-1]]["State"].update(
                Running=False, Status="exited", Pid=0, FinishedAt="finish"
            )
            return b""
        raise AssertionError(args)

    monkeypatch.setattr(host, "docker", docker)
    monkeypatch.setattr(seal_cleanup, "docker", docker)
    monkeypatch.setattr(
        guest_bridge.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(stdout=docker(*argv[1:])),
    )
    monkeypatch.setattr(
        guest_bridge,
        "process_snapshot",
        lambda pid: parent if pid == 99 else {"pid": pid, "start_ticks": 1},
    )
    monkeypatch.setattr(
        seal_cleanup.os, "pidfd_open", lambda pid: os.open(seed, os.O_RDONLY), raising=False
    )

    class Engine:
        async def dispose(self):
            calls.append("engine-close")

    class ClientBoundaryReached(RuntimeError):
        pass

    class Minio:
        def __init__(self, settings, *, real_test_io):
            assert settings.minio_endpoint == "172.20.0.4:9000"
            assert settings.minio_bucket == binding["minio_bucket"]
            assert binding["minio_endpoint"] == "minio:9000"
            assert real_test_io is True
            calls.append("minio-constructed")

        async def init(self):
            calls.append("minio-init")
            raise ClientBoundaryReached("actual observer client boundary reached")

        async def shutdown(self):
            calls.append("minio-close")

    monkeypatch.setattr(observer_resources, "create_async_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(observer_resources, "async_sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(observer_resources, "Minio", Minio)
    parent_budgets, write_budgets = [], []
    real_parents, real_write = seal_cleanup.ReadOnlyParents, seal_cleanup.write_private

    def parents(journals, *, budget=None):
        parent_budgets.append(budget)
        return real_parents(journals, budget=budget)

    def write(path, value, *, budget=None):
        write_budgets.append(budget)
        return real_write(path, value, budget=budget)

    monkeypatch.setattr(seal_cleanup, "ReadOnlyParents", parents)
    monkeypatch.setattr(seal_cleanup, "write_private", write)
    expected = ClientBoundaryReached if endpoint == "172.20.0.4:9000" else ValueError
    with (
        AttemptLedger.create(
            seed.parent / "attempt", {"identity": {"boot_id": process["boot_id"]}}
        ) as ledger,
        pytest.raises(expected, match=r"boundary reached|Minio|MinIO|observer target"),
    ):
        asyncio.run(seal_cleanup.base_cleanup(config, ledger, manifest))
    assert parent_budgets[0].parent is ledger.evidence_owner.budget
    assert write_budgets
    assert all(b is ledger.evidence_owner.budget for b in write_budgets)
    assert calls == (
        ["minio-constructed", "minio-init", "minio-close", "engine-close"]
        if endpoint == "172.20.0.4:9000"
        else []
    )


@pytest.mark.parametrize("mutation", ["none", "hidden-mount", "redirected-journal", "missing-root"])
def test_actual_writable_namespace_matches_inspected_mount(tmp_path, mutation):
    from scripts.execution_capacity.seal_storage import verify_writable_namespace

    source = tmp_path / "volume"
    source.mkdir()
    (source / "operations.sqlite").write_bytes(b"journal")
    proc = tmp_path / "proc"
    process = proc / "31"
    namespace = process / "root"
    namespace.mkdir(parents=True)
    (namespace / "receipts").symlink_to(source, target_is_directory=True)
    mountinfo = "21 1 8:1 /volume /receipts rw - ext4 /dev/vda1 rw\n"
    if mutation == "hidden-mount":
        mountinfo += "22 21 0:99 / /receipts/nested rw - tmpfs tmpfs rw\n"
    elif mutation == "redirected-journal":
        (namespace / "receipts").unlink()
        other = tmp_path / "foreign"
        other.mkdir()
        (namespace / "receipts").symlink_to(other, target_is_directory=True)
    elif mutation == "missing-root":
        (namespace / "receipts").unlink()
    (process / "mountinfo").write_text(mountinfo)
    row = {
        "State": {"Running": True, "Pid": 31},
        "Mounts": [
            {"Destination": "/receipts", "Source": str(source), "RW": True, "Type": "volume"}
        ],
    }
    if mutation == "none":
        verify_writable_namespace(row, proc_root=proc)
    else:
        with pytest.raises((ValueError, FileNotFoundError), match=r"namespace|No such file"):
            verify_writable_namespace(row, proc_root=proc)


@pytest.mark.parametrize("mutation", ["alias", "network", "ambiguous-address", "ambiguous-alias"])
def test_observer_endpoint_retains_original_owned_alias_authority(owned_root, mutation):
    binding, rows = owned_root.binding, owned_root.rows
    minio = rows[binding["minio_container"]]
    provider = rows[binding["provider_container"]]
    if mutation == "alias":
        binding["minio_endpoint"] = "foreign:9000"
    elif mutation == "network":
        minio["NetworkSettings"]["Networks"]["owned"]["NetworkID"] = "f" * 64
    elif mutation == "ambiguous-address":
        provider["NetworkSettings"]["Networks"]["owned"]["IPAddress"] = "172.20.0.4"
    else:
        provider["NetworkSettings"]["Networks"]["owned"]["Aliases"].append("minio")
    with pytest.raises(ValueError, match="MinIO"):
        observer_resources.owned_minio_endpoint(binding, rows)
