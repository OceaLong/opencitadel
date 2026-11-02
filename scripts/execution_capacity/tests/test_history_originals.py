"""Private read acquisition and original historical claim resolution, no runtime."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest


@pytest.mark.asyncio
async def test_actual_read_snapshot_retains_nonobserver_originals_and_empty_reads():
    from scripts.execution_capacity.inventory_sql import read_snapshot

    from app.domain.models.authorization import AuthorizationContext

    calls = []

    class Rows:
        def mappings(self):
            return self

        async def __aiter__(self):
            if False:
                yield None

        async def close(self):
            calls.append("close-stream")

    class DB:
        def __init__(self):
            self.info = {"database_authorization_signing_secret": "fixture-only-signing-material"}

        async def rollback(self):
            calls.append("rollback")

        async def connection(self, *, execution_options):
            assert execution_options == {
                "isolation_level": "REPEATABLE READ",
                "postgresql_readonly": True,
            }
            calls.append("readonly")

        async def execute(self, sql, params):
            if "set_config('app.auth_mode'" in str(sql):
                assert params["auth_signature"]
                assert params["system_actor"] == "execution-kernel"
                calls.append("authorization")
                return None
            assert "row_to_json" in str(sql)
            assert params == {"identity": UUID(int=1)}
            calls.append("preflight")
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {
                        "row_count": 0,
                        "max_bytes": 0,
                        "total_bytes": 0,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "actual-original-snapshot",
                    }
                )
            )

        async def stream(self, sql, params):
            calls.append("stream")
            return Rows()

    @asynccontextmanager
    async def sessions():
        yield DB()

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    budget = EvidenceBudget()
    async with read_snapshot(
        sessions, AuthorizationContext.system("execution-kernel"), budget=budget
    ) as query:
        assert query.budget is budget
        assert (
            await query.rows(
                "empty", "SELECT id FROM original WHERE id=:identity", {"identity": UUID(int=1)}
            )
            == []
        )
        observed = query.originals[0]
        assert observed["owner"] == "readonly-snapshot"
        assert observed["dispatch"] is None
        assert observed["sql_index"] is None
        assert observed["ordinal"] == 0
        assert observed["rows"] == []
        assert observed["parameters"] == {"identity": UUID(int=1)}
        assert observed["parameter_types"] == {"identity": "UUID"}
        assert observed["read"] == query.reads[0]
    assert calls == [
        "rollback",
        "readonly",
        "authorization",
        "preflight",
        "stream",
        "close-stream",
        "rollback",
    ]


@pytest.mark.parametrize("new_claim", [False, True])
def test_unknown_original_claim_requires_its_own_complete_receipt(tmp_path, new_claim):
    import json
    import sqlite3

    from scripts.execution_capacity.broker_inventory import (
        BrokerRequests,
        expected_requests,
        parse_pages,
        request_parents,
        sqlite_pages,
    )
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.physical import physical_parents

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        parent = {
            "scope": "user:owned",
            "namespace": "private-namespace",
            "generation": 1,
            "environment_version": "version",
            "case_slot": {"workspace": "user:owned", "batch_id": "batch"},
        }
        journal.intent("lease", "lease", parent)
        journal.acknowledge("lease", "lease", {"state": "verified_clean"})
        early = {
            "id": "operation",
            "lease_id": "lease",
            "status": "unknown",
            "error": "outcome_unknown",
            "claim_until": None,
            "receipt": None,
            "generation": 1,
            "claim_generation": 1,
            "phase": "cleanup",
            "lease_revision": 1,
        }
        journal.intent("environment_observation", "early", early)
        late = {
            **early,
            "status": "done",
            "error": None,
            "receipt": {"clean": True},
            "claim_generation": 2 if new_claim else 1,
        }
        journal.intent("environment_observation", "late", late)

        def request(claim):
            return {
                "lease": {
                    "id": "lease",
                    "generation": 1,
                    "case_slot": parent["case_slot"],
                    "environment_version": "version",
                },
                "version": {"pinned": "original"},
                "operation": {
                    "id": "operation",
                    "claim_generation": claim,
                    "phase": "cleanup",
                    "lease_revision": 1,
                },
            }

        observer = BrokerRequests(journal)
        old = observer.before(request(1))
        latest = observer.before(request(2)) if new_claim else old
        observer.after(latest, late["receipt"])
        originals = request_parents(expected_requests(journal), journal)
        path = tmp_path / "broker.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE operations(identity TEXT,fingerprint TEXT,result TEXT)")
            db.execute("CREATE TABLE bindings(identity TEXT,fingerprint TEXT)")
            for key, value in originals.operations.items():
                db.execute(
                    "INSERT INTO operations VALUES(?,?,?)",
                    (
                        key,
                        value["fingerprint"],
                        json.dumps(late["receipt"]) if key == latest else None,
                    ),
                )
            for key, value in originals.bindings.items():
                db.execute("INSERT INTO bindings VALUES(?,?)", (key, value))
        actual = parse_pages(sqlite_pages(path))
        if new_claim:
            with pytest.raises(ValueError, match="pending/unknown"):
                physical_parents(journal, actual)
        else:
            physical_parents(journal, actual)
        assert journal.get("environment_observation", "early")["body"] == early
        assert (
            journal.get("broker_request", old)["receipt"] is None
            if new_claim
            else journal.get("broker_request", old)["receipt"] is not None
        )
