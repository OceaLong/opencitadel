"""Private temporary SQLite only; no broker, Docker or provider execution."""

import sqlite3
from hashlib import sha256

import pytest
from scripts.execution_capacity import broker_inventory as source
from scripts.execution_capacity.observers import RecoveryJournal


def database(tmp_path, count):
    path = tmp_path / "broker.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE operations(identity TEXT PRIMARY KEY,fingerprint TEXT,result TEXT)"
        )
        db.execute("CREATE TABLE bindings(identity TEXT PRIMARY KEY,fingerprint TEXT)")
        db.executemany(
            "INSERT INTO operations VALUES(?,?,?)",
            [(f"user:a:l:1:o:{i:06}", "a" * 64, '{"ok":true}') for i in range(count)],
        )
        db.execute("INSERT INTO bindings VALUES(?,?)", ("l:1", "b" * 64))
    return path


def test_complete_snapshot_retains_rows_beyond_old_50000_and_both_tables(tmp_path):
    path = database(tmp_path, 50002)
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    with pytest.raises(ValueError, match="quota"):
        list(source.sqlite_pages(path, page_size=113))
    pages = list(source.sqlite_pages(path, page_size=113, rows_limit=60000))
    result = source.parse_pages(
        pages, budget=EvidenceBudget(bytes_limit=64 * 1024 * 1024, rows_limit=60000)
    )
    assert result.complete
    assert len(result.operations) == 50002
    assert len(result.bindings) == 1
    assert pages[-1]["kind"] == "complete"
    assert sum(p["count"] for p in pages if p["kind"] == "operations") == 50002


@pytest.mark.parametrize(
    "mutation", ["missing_end", "repeat", "count", "reverse", "missing_binding"]
)
def test_incomplete_or_mutated_broker_pages_keep_failure(tmp_path, mutation):
    pages = list(source.sqlite_pages(database(tmp_path, 5), page_size=2))
    if mutation == "missing_end":
        pages.pop()
    elif mutation == "repeat":
        pages.insert(2, pages[1])
    elif mutation == "count":
        pages[-1]["counts"]["operations"] += 1
    elif mutation == "reverse":
        pages[1]["rows"].reverse()
    else:
        pages = [p for p in pages if p["kind"] != "bindings"]
    result = source.parse_pages(pages)
    assert not result.complete
    assert result.errors


def request(claim=1):
    return {
        "lease": {
            "id": "lease",
            "generation": 1,
            "case_slot": {"workspace": "user:owner", "batch_id": "batch"},
            "resources": {},
            "actual_versions": {},
        },
        "operation": {"id": "operation", "claim_generation": claim, "phase": "prepare"},
        "version": {"id": "version", "complete": "pinned"},
    }


def test_original_request_capture_is_durable_before_dispatch_and_never_launders_unknown(tmp_path):
    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        observer = source.BrokerRequests(journal)
        first = observer.before(request())
        assert journal.parent("broker_request", first)["request"] == request()
        second = observer.before(request(2))
        observer.after(second, {"ok": True})
        assert journal.get("broker_request", first)["receipt"] is None
        expected = source.expected_requests(journal)
        assert expected.operations[first]["state"] == "pending"
        assert expected.operations[second]["state"] == "settled"
        assert len(expected.bindings) == 1


def test_identical_receipt_never_bypasses_request_or_binding_fingerprint(tmp_path):
    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        observer = source.BrokerRequests(journal)
        key = observer.before(request())
        observer.after(key, {"ok": True})
        expected = source.expected_requests(journal)
        actual = source.BrokerInventory(
            operations={
                key: {
                    "fingerprint": expected.operations[key]["fingerprint"],
                    "result_sha256": sha256(b'{"ok":true}').hexdigest(),
                }
            },
            bindings=dict(expected.bindings),
            complete=True,
        )
        assert not source.reconcile(actual, expected)
        actual.operations[key]["fingerprint"] = "0" * 64
        assert source.reconcile(actual, expected)
        actual.operations[key]["fingerprint"] = expected.operations[key]["fingerprint"]
        actual.bindings["lease:1"] = "0" * 64
        assert source.reconcile(actual, expected)


def test_unknown_old_claim_and_unrecorded_request_remain_explicit(tmp_path):
    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        observer = source.BrokerRequests(journal)
        key = observer.before(request())
        expected = source.expected_requests(journal)
        actual = source.BrokerInventory(
            operations={
                key: {
                    "fingerprint": expected.operations[key]["fingerprint"],
                    "result_sha256": None,
                },
                "foreign": {"fingerprint": "a" * 64, "result_sha256": "b" * 64},
            },
            bindings=dict(expected.bindings),
            complete=True,
        )
        issues = source.reconcile(actual, expected)
        assert {r["identity"] for r in issues} == {key, "foreign"}
        assert any(r["state"] == "pending" for r in issues)


def test_real_adapter_dispatch_observer_retains_transport_failure(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from app.infrastructure.evaluation.broker_adapter import BrokerEnvironmentAdapter

    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        observer = source.BrokerRequests(journal)

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, *, json, headers):
                assert (
                    journal.parent("broker_request", source.request_identity(json))["request"]
                    == json
                )
                raise httpx.ConnectError("unit transport interruption")

        monkeypatch.setattr(httpx, "AsyncClient", Client)
        adapter = BrokerEnvironmentAdapter(
            broker_url="http://owned",
            broker_token="private",
            allowed_images=[],
            fixture_image="sha256:" + "a" * 64,
            bootstrap_image="sha256:" + "b" * 64,
            request_observer=observer,
        )
        with pytest.raises(Exception, match="outcome_unknown"):
            asyncio.run(adapter.request("lifecycle", request()))
        assert journal.get("broker_request", source.request_identity(request()))["receipt"] is None


def test_exact_request_parent_rejects_same_ids_with_changed_pinned_slot(tmp_path):
    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        observer = source.BrokerRequests(journal)
        body = request()
        key = observer.before(body)
        observer.after(key, {"ok": True})
        journal.intent(
            "lease",
            "lease",
            {
                "scope": "user:owner",
                "generation": 1,
                "environment_version": "version",
                "case_slot": {"workspace": "user:owner", "batch_id": "different"},
            },
        )
        with pytest.raises(ValueError, match="slot"):
            source.request_parents(source.expected_requests(journal), journal)
