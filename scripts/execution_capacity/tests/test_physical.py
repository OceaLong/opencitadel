"""Exact physical cleanup proof over substituted Docker readbacks."""

import json
from uuid import uuid4

import pytest
from scripts.execution_capacity.observers import RecoveryJournal
from scripts.execution_capacity.physical import verify_physical_clean


@pytest.mark.parametrize(
    "fault", [None, "pending", "foreign", "present", "fingerprint", "binding", "missing_request"]
)
def test_broker_results_and_exact_namespaces_required(tmp_path, fault):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    with RecoveryJournal(root) as journal:
        lease, operation = str(uuid4()), str(uuid4())
        journal.intent("lease", lease, {"scope": "user:owned", "namespace": "e04-exact"})
        journal.acknowledge("lease", lease, {"state": "verified_clean"})
        receipt = {"clean": True}
        journal.intent(
            "environment_observation",
            operation,
            {
                "id": operation,
                "lease_id": lease,
                "status": "done",
                "error": None,
                "claim_until": None,
                "receipt": receipt,
                "generation": 1,
                "claim_generation": 1,
            },
        )
        identity = f"user:owned:{lease}:1:{operation}:1"

        import sqlite3

        from scripts.execution_capacity.broker_inventory import BrokerRequests, sqlite_pages

        from app.domain.evaluation.configuration import digest as request_digest

        request = {
            "lease": {"id": lease, "generation": 1, "case_slot": {"workspace": "user:owned"}},
            "operation": {"id": operation, "claim_generation": 1},
            "version": {"pinned": "actual"},
        }
        if fault != "missing_request":
            observer = BrokerRequests(journal)
            observer.after(observer.before(request), receipt)
        path = tmp_path / "broker.sqlite"
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE operations(identity TEXT PRIMARY KEY,fingerprint TEXT,result TEXT)"
            )
            db.execute("CREATE TABLE bindings(identity TEXT PRIMARY KEY,fingerprint TEXT)")
            db.execute(
                "INSERT INTO operations VALUES(?,?,?)",
                (
                    "foreign" if fault == "foreign" else identity,
                    "0" * 64 if fault == "fingerprint" else request_digest(request),
                    None if fault == "pending" else json.dumps(receipt),
                ),
            )
            db.execute(
                "INSERT INTO bindings VALUES(?,?)",
                (
                    lease + ":1",
                    "0" * 64
                    if fault == "binding"
                    else request_digest(
                        {"slot": request["lease"]["case_slot"], "version": request["version"]}
                    ),
                ),
            )

        def docker(*args):
            if args[0] == "exec":
                return ("\n".join(json.dumps(row) for row in sqlite_pages(path)) + "\n").encode()
            assert args[-1] == "label=opencitadel.e04.namespace=e04-exact"
            return b"exact-resource-id" if fault == "present" else b""

        observations = []
        if fault:
            with pytest.raises(ValueError, match="restoration withheld"):
                verify_physical_clean(
                    {"broker": {"container_id": "broker"}},
                    journal,
                    docker,
                    observations=observations,
                )
        else:
            result = verify_physical_clean(
                {"broker": {"container_id": "broker"}}, journal, docker, observations=observations
            )
            assert result["broker_operations"] == 1
            assert "e04-exact" not in str(result), "private namespace leaked through host receipt"
            assert len(observations) == 2, "original namespace responses discarded"
            from scripts.execution_capacity.broker_inventory import parse_pages
            from scripts.execution_capacity.physical import replay_physical

            broker = parse_pages(sqlite_pages(path))
            replay = replay_physical(journal, broker, observations)
            assert replay["retained_resources"] == 0
            changed = [dict(row) for row in observations]
            changed[0]["response"] = "owned-resource"
            with pytest.raises(ValueError, match="physical resources"):
                replay_physical(journal, broker, changed)
            with pytest.raises(ValueError, match="observation coverage"):
                replay_physical(journal, broker, changed[1:])
            assert result["retained_resources"] == 0


def test_physical_snapshot_detaches_nested_rows_and_late_owner_thread_changes(tmp_path):
    from scripts.execution_capacity.physical import PhysicalJournalSnapshot

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent(
            "lease", "original", {"scope": "user:owned", "nested": {"value": "original"}}
        )
        snapshot = PhysicalJournalSnapshot.capture(journal)
        journal.acknowledge("lease", "original", {"state": "verified_clean"})
        journal.intent("lease", "late", {"scope": "user:owned"})
        detached = snapshot.records("lease")
        detached[0][1]["body"]["nested"]["value"] = "mutated consumer copy"
        assert snapshot.records("lease") == [
            (
                "original",
                {"body": {"scope": "user:owned", "nested": {"value": "original"}}, "receipt": None},
            )
        ]
        assert len(list(journal.records("lease"))) == 2
