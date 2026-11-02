"""Immutable explicit producer bounds precede any service construction."""

from copy import deepcopy

import pytest


def limits():
    return {
        "bytes_limit": 16 * 1024 * 1024,
        "rows_limit": 16384,
        "row_limit": 1024 * 1024,
        "index_bytes": 64 * 1024,
    }


@pytest.mark.parametrize(
    "fault", ["missing", "extra", "bool", "float", "zero", "overflow", "row", "page", "index"]
)
def test_evidence_limits_reject_nonexact_or_unsupported_values(fault):
    from scripts.execution_capacity.evidence_bounds import parse_evidence_limits

    value = deepcopy(limits())
    if fault == "missing":
        del value["index_bytes"]
    elif fault == "extra":
        value["caller"] = True
    elif fault in ("bool", "float", "zero", "overflow"):
        value["rows_limit"] = {"bool": True, "float": 1.0, "zero": 0, "overflow": 2**64}[fault]
    elif fault == "row":
        value["row_limit"] = value["bytes_limit"] + 1
    elif fault == "page":
        value["index_bytes"] += 1
    else:
        value["index_bytes"] = 4096
    with pytest.raises(ValueError, match="limit"):
        parse_evidence_limits(value)


def test_actual_unit_owner_uses_explicit_bounds_and_closes_on_exit(tmp_path):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    with EvidenceOwner.from_limits(tmp_path, limits()) as owner:
        assert owner.journal.root == tmp_path / "c2c-originals"
        assert owner.budget.bytes_limit == limits()["bytes_limit"]
        assert owner.journal.index_bytes == limits()["index_bytes"]
        owner.journal.begin("sql", {"dispatched": False})
        journal = owner.journal
    assert journal.closed
    assert (tmp_path / "c2c-originals" / "occurrences.jsonl").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_base_cleanup_creates_bound_owner_before_delegate_and_closes(
    tmp_path, monkeypatch, failure
):
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity import seal_cleanup

    captured = []

    async def consume(config, ledger, manifest, evidence_owner):
        captured.append(evidence_owner.journal)
        assert ledger.evidence_owner is evidence_owner
        assert evidence_owner.limits == limits()
        assert evidence_owner.journal.index.count("begin:cleanup") == 1
        if failure:
            raise ValueError("original fixture failure")
        return {"retained": True}

    monkeypatch.setattr(seal_cleanup, "_base_cleanup", consume, raising=False)
    config = {"evidence_root": str(tmp_path), "evidence_limits": limits()}
    if failure:
        with pytest.raises(ValueError, match="original fixture"):
            asyncio.run(seal_cleanup.base_cleanup(config, SimpleNamespace(), {}))
        assert (tmp_path / "c2c-failed-originals.json").is_file()
    else:
        assert asyncio.run(seal_cleanup.base_cleanup(config, SimpleNamespace(), {})) == {
            "retained": True
        }
    assert captured
    assert captured[0].closed


def test_missing_limits_fail_before_base_delegate(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity import seal_cleanup

    async def forbidden(*args):
        raise AssertionError("service construction reached")

    monkeypatch.setattr(seal_cleanup, "_base_cleanup", forbidden, raising=False)
    with pytest.raises(ValueError, match="limits"):
        asyncio.run(
            seal_cleanup.base_cleanup({"evidence_root": str(tmp_path)}, SimpleNamespace(), {})
        )
    assert not (tmp_path / "c2c-originals").exists()


def test_observer_rejects_missing_owner_before_service_constructor(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity import observer_resources

    def forbidden(*args, **kwargs):
        raise AssertionError("service constructor reached")

    monkeypatch.setattr(observer_resources, "create_async_engine", forbidden)
    monkeypatch.setattr(observer_resources, "Minio", forbidden)
    settings = SimpleNamespace(
        env="test",
        storage_provider="minio",
        minio_endpoint="private",
        minio_bucket="private",
        sqlalchemy_database_uri="unused",
    )
    binding = {"environment": "test", "minio_endpoint": "private", "minio_bucket": "private"}

    async def run():
        async with observer_resources.open_observers(settings, binding):
            raise AssertionError("observer yielded")

    with pytest.raises(ValueError, match="owner"):
        asyncio.run(run())


@pytest.mark.parametrize("entry", ["phase", "dispatcher"])
def test_guest_missing_limits_rejects_before_boot_or_artifact_io(monkeypatch, entry):
    from scripts.execution_capacity import guest_seal, guest_seal_entry

    if entry == "phase":
        with pytest.raises(ValueError, match="limits"):
            guest_seal.phase(
                {"identity": {}, "phase": "cleanup", "config_digest": "unused"}, {}, {}
            )
    else:
        monkeypatch.setattr(guest_seal_entry.sys, "argv", ["entry", '{"phase":"read"}'])
        monkeypatch.setattr(
            guest_seal_entry, "private_json", lambda path: {"protocol_id": "fixture"}
        )
        with pytest.raises(ValueError, match="limits"):
            guest_seal_entry.main()


@pytest.mark.parametrize("phase", ["cleanup", "read"])
@pytest.mark.parametrize(
    "fault", [None, "missing-plan", "missing-echo", "changed-echo", "bool-echo"]
)
def test_host_seal_requires_committed_limits_and_exact_guest_echo(
    tmp_path, monkeypatch, phase, fault
):
    from threading import RLock

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_session import GuestSession

    rule = {
        "phase_timeout_seconds": 30,
        "config_digest": "a" * 64,
        "helper_sha256": "b" * 64,
        "python_sha256": "c" * 64,
        "export": {"protocol_id": "fixture"},
        "evidence_limits": limits(),
    }
    response = {
        "phase": phase,
        "config_digest": "a" * 64,
        "seal_helper_sha256": "b" * 64,
        "observer_python_sha256": "c" * 64,
        "protocol_id": "fixture",
        "evidence_limits": limits(),
    }
    if fault == "missing-plan":
        del rule["evidence_limits"]
    elif fault == "missing-echo":
        del response["evidence_limits"]
    elif fault == "changed-echo":
        response["evidence_limits"]["bytes_limit"] *= 2
    elif fault == "bool-echo":
        rule["evidence_limits"]["rows_limit"] = 1
        response["evidence_limits"]["rows_limit"] = True
    with AttemptLedger.create(tmp_path / "host", {"seal": rule}) as ledger:
        session = object.__new__(GuestSession)
        session.ledger, session.identity, session.lock = ledger, {"fixture": "identity"}, RLock()
        calls = []

        def command(*args, **kwargs):
            calls.append(True)
            return response

        monkeypatch.setattr(session, "_command", command)
        kwargs = {"artifact": "cleanup", "offset": 0} if phase == "read" else {}
        if fault:
            with pytest.raises(ValueError, match="limits"):
                session.seal_phase(phase, **kwargs)
            assert not ledger.records("guest-seal-result")
            if fault == "missing-plan":
                assert not calls
                assert not ledger.records("guest-seal-intent")
        else:
            assert session.seal_phase(phase, **kwargs) == response
            assert len(ledger.records("guest-seal-result")) == 1


def test_actual_guest_read_echo_is_bound_to_config_digest(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from scripts.execution_capacity import guest_seal_entry as entry

    executable = b"pinned fixture observer executable"
    config = {
        "protocol_id": "fixture",
        "identity": {},
        "evidence_limits": limits(),
        "observer_python_sha256": hashlib.sha256(executable).hexdigest(),
        "evidence_root": str(tmp_path),
    }
    config_digest = hashlib.sha256(entry.encoded(config)).hexdigest()
    request = {
        "identity": {"boot_id": "fixture-boot"},
        "phase": "read",
        "config_digest": config_digest,
        "artifact": "cleanup",
        "offset": 0,
    }
    artifact = tmp_path / "cleanup.json"
    artifact.write_bytes(b'{"retained":true}')
    artifact.chmod(0o600)
    read_bytes, read_text, fstat = Path.read_bytes, Path.read_text, entry.os.fstat
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: executable if str(path) == "/proc/self/exe" else read_bytes(path),
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            "fixture-boot"
            if str(path) == "/proc/sys/kernel/random/boot_id"
            else read_text(path, *args, **kwargs)
        ),
    )

    def root_stat(fd):
        info = fstat(fd)
        return SimpleNamespace(
            **{
                name: (0 if name == "st_uid" else getattr(info, name))
                for name in (
                    "st_uid",
                    "st_mode",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            }
        )

    monkeypatch.setattr(entry.os, "fstat", root_stat)
    monkeypatch.setattr(entry, "private_json", lambda path: deepcopy(config))
    output = io.BytesIO()
    monkeypatch.setattr(
        entry,
        "sys",
        SimpleNamespace(argv=["entry", json.dumps(request)], stdout=SimpleNamespace(buffer=output)),
    )
    entry.main()
    response = json.loads(output.getvalue())
    assert response["evidence_limits"] == limits()
    assert response["config_digest"] == config_digest
    assert response["size_bytes"] == 17
    config["evidence_limits"]["bytes_limit"] *= 2
    with pytest.raises(ValueError, match="identity/config"):
        entry.main()
