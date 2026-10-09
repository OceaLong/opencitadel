"""Host protocol tests substitute Docker; they never operate a container."""

import pytest
from scripts.execution_capacity.host import HostError, OwnedDeployment


class Journal:
    def __init__(self):
        self.rows = {}

    def intent(self, kind, identity, body):
        self.rows[kind, identity] = {"body": body}

    def get(self, kind, identity):
        return self.rows.get((kind, identity))


def test_unverified_convergence_withholds_restart(monkeypatch):
    deployment = OwnedDeployment({"invocation": "test"}, Journal())
    deployment.original = {"original": True}
    calls = []
    monkeypatch.setattr("scripts.execution_capacity.host.docker", lambda *args: calls.append(args))
    with pytest.raises(HostError, match="withheld"):
        deployment.restore(converged=False)
    assert calls == []


def test_resume_preserves_original_running_states(monkeypatch):
    journal = Journal()
    journal.intent(
        "host", "test", {"running": {"old": True, "stopped": False}, "network_id": "network"}
    )
    deployment = OwnedDeployment({"invocation": "test", "network_id": "network"}, journal)
    monkeypatch.setattr(
        deployment,
        "verify",
        lambda **kwargs: (
            {"old": False, "stopped": False},
            {key: {"Config": {}, "HostConfig": {}} for key in ("old", "stopped")},
        ),
    )
    monkeypatch.setattr("scripts.execution_capacity.host.docker", lambda *args: None)
    deployment.stop()
    assert deployment.original == {"old": True, "stopped": False}


def test_lost_create_response_recovers_exact_inspected_identity(monkeypatch):
    from scripts.execution_capacity.host import verify_created_child

    expected = {
        "name": "exact-random-name",
        "image": "sha256:" + "b" * 64,
        "network": "n",
        "user": "1000:1000",
        "labels": {"attempt": "unique"},
        "mounts": {"/private": ["bind", "/exact/private", True]},
    }
    actual = {
        "Id": "a" * 64,
        "Name": "/exact-random-name",
        "Image": expected["image"],
        "State": {"Running": False, "Status": "created"},
        "Config": {
            "Entrypoint": ["/app/.venv/bin/python"],
            "Cmd": ["-m", "scripts.execution_capacity.child"],
            "User": "1000:1000",
            "Labels": {"attempt": "unique"},
        },
        "Mounts": [
            {"Destination": "/private", "Type": "bind", "Source": "/exact/private", "RW": True}
        ],
        "HostConfig": {"Privileged": False},
        "NetworkSettings": {"Ports": {}, "Networks": {"test": {"NetworkID": "n"}}},
    }
    assert verify_created_child(actual, expected) == "a" * 64
    journal = Journal()
    journal.intent("child", expected["name"], expected)
    journal.records = lambda kind: [(expected["name"], journal.get("child", expected["name"]))]
    journal.acknowledge = lambda kind, identity, receipt: journal.rows[kind, identity].update(
        receipt=receipt
    )
    selected = []

    def inspect(kind, identity):
        selected.append((kind, identity))
        return actual

    monkeypatch.setattr("scripts.execution_capacity.host.inspect", inspect)
    deployment = OwnedDeployment({}, journal)
    assert selected == [("container", "exact-random-name")]
    assert list(deployment.children) == ["a" * 64]
    actual["Mounts"][0]["Source"] = "/foreign"
    with pytest.raises(HostError, match="configuration differs"):
        verify_created_child(actual, expected)
    actual["Mounts"][0]["Source"] = "/exact/private"
    actual["State"]["Status"] = "exited"
    with pytest.raises(HostError, match="configuration differs"):
        verify_created_child(actual, expected)


@pytest.mark.parametrize("physical_failure", [False, True])
@pytest.mark.parametrize("retention_failure", [False, True])
def test_historical_host_receipt_keeps_namespace_private_and_both_errors(
    tmp_path, monkeypatch, physical_failure, retention_failure
):
    import json

    from scripts.execution_capacity.host import retain_physical_receipt
    from scripts.execution_capacity.observers import RecoveryJournal

    root, writers = tmp_path / "private", tmp_path / "writers"
    root.mkdir(mode=0o700)
    writers.mkdir(mode=0o700)
    receipt = {"status": "corpus_ready", "fixture_complete": False}

    def acquire(binding, journal, docker, *, observations):
        observations.append({"request": ["private-namespace"], "response": "private-result"})
        if physical_failure:
            raise ValueError("physical acquisition failed")
        return {"retained_resources": 0}

    monkeypatch.setattr("scripts.execution_capacity.physical.verify_physical_clean", acquire)
    with RecoveryJournal(root) as journal, RecoveryJournal(writers) as writer_journal:
        if retention_failure:

            def refuse(*args):
                raise OSError("private retention failed")

            monkeypatch.setattr(journal, "intent", refuse)
        if physical_failure and retention_failure:
            with pytest.raises(BaseExceptionGroup, match="observation and retention") as error:
                retain_physical_receipt(receipt, {}, journal, writer_journal)
            assert [type(e) for e in error.value.exceptions] == [ValueError, OSError]
        elif physical_failure:
            with pytest.raises(ValueError, match="physical acquisition"):
                retain_physical_receipt(receipt, {}, journal, writer_journal)
        elif retention_failure:
            with pytest.raises(OSError, match="private retention"):
                retain_physical_receipt(receipt, {}, journal, writer_journal)
        else:
            retain_physical_receipt(receipt, {}, journal, writer_journal)
        assert "private-namespace" not in json.dumps(receipt)
        assert "private-result" not in json.dumps(receipt)
        assert ("physical_cleanup" in receipt) == (not physical_failure and not retention_failure)
        if not retention_failure:
            rows = list(journal.records("physical_observation"))
            assert len(rows) == 1
            assert rows[0][1]["body"]["observations"][0]["request"] == ["private-namespace"]
            assert rows[0][1]["body"]["error"] == ("ValueError" if physical_failure else None)
