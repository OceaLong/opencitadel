"""Concrete stopped-incarnation and normal service-exit boundaries, no Docker."""

import copy
import json
from types import SimpleNamespace

import pytest


def row():
    return {
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "Path": "redis-server",
        "Args": [],
        "Config": {"User": "redis", "Env": [], "Hostname": "redis"},
        "Mounts": [],
        "HostConfig": {"Privileged": False, "RestartPolicy": {"Name": "no"}},
        "NetworkSettings": {"Networks": {"owned": {"NetworkID": "c" * 64}}},
        "State": {
            "Running": True,
            "Status": "running",
            "Pid": 31,
            "StartedAt": "start",
            "FinishedAt": "",
            "ExitCode": 0,
            "Dead": False,
            "OOMKilled": False,
        },
    }


def test_staged_inspection_keeps_identity_across_exit(monkeypatch):
    from scripts.execution_capacity import guest_bridge as bridge
    from scripts.execution_capacity.guest_seal import incarnation, verify_incarnation

    actual = row()
    expected = {
        "id": actual["Id"],
        "image": actual["Image"],
        "argv": [actual["Path"]],
        "user": "redis",
        "mounts": [],
        "env_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        "network_ids": ["c" * 64],
    }
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=json.dumps([actual]).encode()),
    )
    saved = incarnation(actual)
    actual["State"].update(Running=False, Status="exited", Pid=0, FinishedAt="finish")
    assert bridge.inspect_owned(expected, running=False)["State"]["Pid"] == 0
    verify_incarnation(actual, saved, exited=True)
    actual["State"]["StartedAt"] = "replacement"
    with pytest.raises(ValueError, match="incarnation"):
        verify_incarnation(actual, saved, exited=True)


def test_stop_uses_only_exact_normal_signal_and_retains_unclean_exit(monkeypatch):
    from scripts.execution_capacity import guest_seal as seal

    actual = row()
    original = seal.incarnation(actual)
    intents, commands = [], []
    process = {"pid": 31, "start_ticks": 12}

    def execute(argv, **kw):
        commands.append(argv)
        assert intents
        assert intents[0][0] == "service-stop-intent"
        assert argv == ["/usr/bin/docker", "stop", "--signal", "SIGTERM", "--time", "-1", "a" * 64]
        actual["State"].update(
            Running=False, Status="exited", Pid=0, FinishedAt="finish", ExitCode=137
        )
        return b""

    monkeypatch.setattr(seal, "run", execute)
    monkeypatch.setattr(seal, "inspect_container", lambda _id: copy.deepcopy(actual))
    monkeypatch.setattr(seal, "process_snapshot", lambda _pid: process)
    with pytest.raises(ValueError, match="clean"):
        seal.stop_service("redis", original, process, lambda k, v: intents.append((k, v)))
    assert len(commands) == 1
    assert intents[-1][0] == "service-exit"


def test_pg_control_requires_exact_cluster_and_shutdown_state():
    from scripts.execution_capacity.guest_seal import parse_control

    good = "Database system identifier: 12345\nDatabase cluster state: shut down\n"
    assert parse_control(good, "12345")["state"] == "shut down"
    for bad in (
        good.replace("shut down", "in production"),
        good.replace("12345", "12346"),
        good + "Database cluster state: shut down\n",
    ):
        with pytest.raises(ValueError, match=r"ambiguous|offline"):
            parse_control(bad, "12345")


def test_preexisting_stopped_writer_needs_prior_live_capture(tmp_path):
    from scripts.execution_capacity.writer_lifecycle import verify_prior_capture

    body = {"pid": 42, "start_ticks": 123, "pid_namespace": 7, "boot_id": "boot"}
    actual = row()
    actual["State"].update(Running=False, Status="exited", Pid=0, FinishedAt="finish")
    with pytest.raises(ValueError, match="capture"):
        verify_prior_capture(actual, body, None)


def test_observer_preserves_primary_and_both_close_errors(tmp_path, monkeypatch):
    import asyncio

    from scripts.execution_capacity import observer_resources as observers

    class Engine:
        async def dispose(self):
            raise RuntimeError("engine-close")

    class Storage:
        def __init__(self, *args, **kwargs):
            pass

        async def init(self):
            pass

        async def shutdown(self):
            raise OSError("storage-close")

    monkeypatch.setattr(observers, "create_async_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(observers, "async_sessionmaker", lambda *a, **k: object())
    monkeypatch.setattr(observers, "Minio", Storage)
    settings = SimpleNamespace(
        env="test",
        storage_provider="minio",
        minio_endpoint="owned",
        minio_bucket="bucket",
        sqlalchemy_database_uri="owned",
        database_authorization_signing_secret="private",
    )
    binding = {"environment": "test", "minio_endpoint": "owned", "minio_bucket": "bucket"}

    async def scenario():
        async with observers.open_observers(settings, binding, evidence_owner=owner):
            raise ValueError("primary")

    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    with (
        EvidenceOwner(original_root=tmp_path / "c2c-originals", index_bytes=64 * 1024) as owner,
        pytest.raises(BaseExceptionGroup) as failure,
    ):
        asyncio.run(scenario())
    assert [str(e) for e in failure.value.exceptions] == [
        "primary",
        "storage-close",
        "engine-close",
    ]
