"""Original controls survive acquisition and drive the same offline predicates."""

import base64
import copy
from hashlib import sha256

import pytest
from scripts.execution_capacity import storage_inventory as storage
from scripts.execution_capacity.test_storage_inventory import SDK, objects, uploads


def test_actual_s3_pages_replay_and_changed_control_is_not_clean():
    actual = storage.MinioInventory(SDK([objects(), uploads()]), "owned").read()
    assert all("raw_base64" in page for page in actual.pages), "original page bytes discarded"
    assert storage.replay_storage(actual, "owned").safe() == actual.safe()
    changed = copy.deepcopy(actual)
    page = changed.pages[1]
    raw = base64.b64decode(page["raw_base64"]).replace(b"<IsTruncated>false", b"<IsTruncated>true")
    page.update(
        raw_base64=base64.b64encode(raw).decode(), sha256=sha256(raw).hexdigest(), bytes=len(raw)
    )
    with pytest.raises(ValueError, match="storage replay"):
        storage.replay_storage(changed, "owned")


def test_storage_retention_quota_fails_before_next_request():
    sdk = SDK([objects(), uploads()])
    reader = storage.MinioInventory(sdk, "owned", retained_bytes=64)
    result = reader.read()
    assert not result.complete
    assert result.errors == [{"stage": "storage-read", "type": "EvidenceQuotaError"}]
    assert len(sdk.queries) == 1


def test_shared_lease_predicate_rejects_wrong_attempt_and_unknown_old_operation():
    from types import SimpleNamespace
    from uuid import uuid4, uuid5

    from scripts.execution_capacity import batch_facts

    run = uuid4()
    lease = SimpleNamespace(
        id=uuid5(run, "environment"),
        generation=2,
        case_slot=SimpleNamespace(workspace="user:owned"),
        state="verified_clean",
    )
    operation = {"status": "done", "error": None, "receipt": {"ok": True}, "claim_until": None}
    predicate = getattr(batch_facts, "lease_observation_clean", None)
    assert callable(predicate), "lease predicate still hidden in acquisition"
    assert predicate(lease, [{"run_id": str(run), "attempt": 1}], [operation], "user:owned")
    assert not predicate(
        lease,
        [{"run_id": str(run), "attempt": 1}],
        [operation, {**operation, "receipt": None}],
        "user:owned",
    )
    with pytest.raises(ValueError, match="exact actual case attempt"):
        predicate(lease, [{"run_id": str(run), "attempt": 0}], [operation], "user:owned")


def test_inventory_query_bounds_stream_without_eager_result_and_retains_operands():
    import asyncio

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
    from scripts.execution_capacity.inventory_sql import InventoryQueries

    class Rows:
        def mappings(self):
            return self

        async def __aiter__(self):
            yield {"id": "first"}
            yield {"id": "second"}

        async def close(self):
            pass

    class DB:
        async def execute(self, sql, params):
            from types import SimpleNamespace

            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {
                        "row_count": 2,
                        "max_bytes": 20,
                        "total_bytes": 40,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "actual",
                    }
                )
            )

        async def stream(self, sql, params):
            return Rows()

    async def check():
        query = InventoryQueries(DB(), budget=EvidenceBudget(rows_limit=1))
        with pytest.raises(EvidenceQuotaError):
            await query.rows("attempts", "SELECT id FROM attempts", {"batch": "actual"})
        assert query.reads[0]["parameters"] == {"batch": "actual"}
        assert query.reads[0]["error"] == "EvidenceQuotaError"
        assert query.reads[0]["end_ns"] >= query.reads[0]["start_ns"]

    asyncio.run(check())


def test_server_preflight_rejects_oversize_before_original_typed_stream():
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError
    from scripts.execution_capacity.inventory_sql import InventoryQueries

    class DB:
        async def execute(self, sql, params):
            assert "row_to_json" in str(sql)
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {
                        "row_count": 1,
                        "max_bytes": 2**30,
                        "total_bytes": 2**30,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "actual",
                    }
                )
            )

        async def stream(self, sql, params):
            pytest.fail("oversize original payload reached driver")

    async def check():
        with pytest.raises(EvidenceQuotaError):
            await InventoryQueries(DB()).rows("original", "SELECT payload FROM original")

    asyncio.run(check())


def test_original_uuid_decimal_values_survive_preflight_stream():
    import asyncio
    from decimal import Decimal
    from types import SimpleNamespace
    from uuid import uuid4

    from scripts.execution_capacity.inventory_sql import InventoryQueries

    value = {"id": uuid4(), "money": Decimal("0.000000000000000000000000000000000001")}
    calls = []

    class Rows:
        def mappings(self):
            return self

        async def __aiter__(self):
            yield value

        async def close(self):
            calls.append("closed")

    class DB:
        async def execute(self, sql, params):
            calls.append("preflight")
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {
                        "row_count": 1,
                        "max_bytes": 200,
                        "total_bytes": 200,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "actual",
                    }
                )
            )

        async def stream(self, sql, params):
            calls.append("original")
            return Rows()

    rows = asyncio.run(InventoryQueries(DB()).rows("money", "SELECT id,money FROM original"))
    assert rows[0]["id"] is value["id"]
    assert rows[0]["money"] is value["money"]
    assert calls == ["preflight", "original", "closed"]


@pytest.mark.parametrize(
    "preflight",
    [
        {"row_count": None, "max_bytes": 0, "total_bytes": 0},
        {"row_count": False, "max_bytes": 0, "total_bytes": 0},
        {"row_count": 0, "max_bytes": 1, "total_bytes": 1},
        {"row_count": 1, "max_bytes": 0, "total_bytes": 0},
        {"row_count": 1, "max_bytes": 1, "total_bytes": 2},
    ],
)
def test_invalid_preflight_never_reads_original(preflight):
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity.inventory_sql import InventoryQueries

    class DB:
        async def execute(self, sql, params):
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {
                        **preflight,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "actual",
                    }
                )
            )

        async def stream(self, sql, params):
            pytest.fail("invalid preflight allowed original stream")

    with pytest.raises(ValueError, match="preflight"):
        asyncio.run(InventoryQueries(DB()).rows("original", "SELECT * FROM original"))


@pytest.mark.parametrize("wrong_attempt", [False, True])
def test_environment_acquisition_retains_original_reads_and_failure(
    tmp_path, monkeypatch, wrong_attempt
):
    import asyncio
    from contextlib import asynccontextmanager
    from uuid import uuid4, uuid5

    from scripts.execution_capacity import batch_facts, inventory_sql
    from scripts.execution_capacity.observers import RecoveryJournal

    from app.domain.evaluation.environment import EnvironmentLease
    from app.domain.models.scope import OwnerScope

    run, batch, case, config, version, operation = (uuid4() for _ in range(6))
    lease = EnvironmentLease(
        id=uuid5(run, "environment"),
        environment_version=version,
        case_slot={
            "workspace": "user:owned",
            "batch_id": batch,
            "case_id": case,
            "config_version": config,
            "repeat": 1,
        },
        generation=1,
        revision=2,
        state="verified_clean",
    )
    original = lease.model_dump(mode="json")
    operation_row = {
        "id": str(operation),
        "lease_id": str(lease.id),
        "status": "done",
        "error": None,
        "claim_until": None,
        "receipt": {"observed": True},
    }
    calls = []

    def rows(sql):
        if sql == inventory_sql.IDENTITY:
            return [{"database_name": "owned", "database_system_identifier": "1"}]
        if sql == batch_facts.LEASE_SQL:
            return [original]
        if sql == batch_facts.ATTEMPT_SQL:
            return [{"run_id": run, "attempt": 1 if wrong_attempt else 0}]
        if sql == batch_facts.OPERATION_SQL:
            return [operation_row]
        raise AssertionError(sql)

    class Result:
        def __init__(self, data):
            self.data = data

        def mappings(self):
            return self

        def one(self):
            return self.data[0]

        async def __aiter__(self):
            for row in self.data:
                yield row

        async def close(self):
            pass

    class DB:
        async def rollback(self):
            calls.append("rollback")

        async def connection(self, *, execution_options):
            assert execution_options == {
                "isolation_level": "REPEATABLE READ",
                "postgresql_readonly": True,
            }
            calls.append("snapshot")

        async def execute(self, sql, params):
            original_sql = str(sql).split("FROM (", 1)[1].rsplit(") AS q", 1)[0]
            data = rows(original_sql)
            calls.append("preflight")
            return Result(
                [
                    {
                        "row_count": len(data),
                        "max_bytes": 1024,
                        "total_bytes": 1024 * len(data),
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "same",
                    }
                ]
            )

        async def stream(self, sql, params):
            calls.append("stream")
            return Result(rows(str(sql)))

    @asynccontextmanager
    async def sessions():
        yield DB()

    async def authorize(db, authorization):
        calls.append("authorize")

    monkeypatch.setattr(inventory_sql, "configure_session_authorization", authorize)
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("batch", batch, {"scope": "user:owned"})
        facts = batch_facts.BatchFacts(
            sessions, None, journal, OwnerScope.personal("owned"), None, batch_id=batch
        )
        if wrong_attempt:
            with pytest.raises(ValueError, match="exact actual case attempt"):
                asyncio.run(facts.environments(final=True))
        else:
            leases, clean = asyncio.run(facts.environments(final=True))
            assert clean
            assert leases == [lease]
        retained = list(journal.records("environment_read"))
        assert len(retained) == 1
        body = retained[0][1]["body"]
        assert body["error"] == ("ValueError" if wrong_attempt else None)
        assert body["leases"] == [original]
        assert body["attempts"][str(lease.id)] == [
            {"run_id": str(run), "attempt": 1 if wrong_attempt else 0}
        ]
        assert body["operations"][str(lease.id)] == [operation_row]
        assert [r["name"] for r in body["reads"]] == [
            "database",
            "leases",
            "attempts:" + str(lease.id),
            "operations:" + str(lease.id),
        ]
        assert body["reads"][2]["parameters"] == {
            "scope": "user:owned",
            "batch": str(batch),
            "case": str(case),
            "config": str(config),
            "repeat": 0,
        }
        assert all(r["preflight"]["snapshot"] == "same" for r in body["reads"])
        assert calls == [
            "rollback",
            "snapshot",
            "authorize",
            *[x for _ in range(4) for x in ("preflight", "stream")],
            "rollback",
        ]


def test_retained_query_operands_never_enter_source_safe_export():
    from scripts.execution_capacity.inventory import SourceInventory

    original = {
        "name": "objects",
        "parameters": {"key": "private-user-filename", "nested": {"credential": "private-secret"}},
        "parameter_digest": "a" * 64,
        "rows": 0,
    }
    source = SourceInventory(database={"reads": [original]}, reads_complete=True)
    public = source.safe()
    assert "private-user-filename" not in str(public)
    assert "private-secret" not in str(public)
    assert source.database["reads"][0]["parameters"]["nested"]["credential"] == "private-secret"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing_clock",
        "duplicate_clock",
        "different_clock",
        "invalid_clock",
        "reservation",
        "path",
        "plan",
    ],
)
def test_offline_round_predicate_uses_only_retained_original_records(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    from uuid import uuid4

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_round import reserve_round, verify_round_records

    monkeypatch.setattr(
        "scripts.execution_capacity.attempt.host_clock",
        lambda: {
            "boot_id": "original",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )
    round_id = str(uuid4())
    plan = {"attempt_id": "parent", "samples": [{"sample_id": "s", "physical_window_id": "w"}]}
    child_plan = {
        "round": {
            "parent_attempt_id": "parent",
            "round_id": round_id,
            "sample_id": "s",
            "window_id": "w",
        }
    }
    with AttemptLedger.create(tmp_path / "parent", plan) as parent:
        target = parent.root / "rounds" / round_id
        binding = reserve_round(parent, child_plan, target, seal_digest="a" * 64)
        target.parent.mkdir(mode=0o700)
        with AttemptLedger.create(target, child_plan) as child:
            child.bind_clock()
            monkeypatch.setattr(
                "scripts.execution_capacity.attempt.host_clock",
                lambda: pytest.fail("offline proof observed current clock"),
            )
            parent_rows = {
                kind: list(parent.records(kind))
                for kind in ("host-clock", "round-reserved", "reserved")
            }
            child_rows = {"host-clock": list(child.records("host-clock"))}
            left = SimpleNamespace(
                plan=parent.plan, root=parent.root, records=lambda kind: parent_rows.get(kind, [])
            )
            right = SimpleNamespace(
                plan=child.plan, root=child.root, records=lambda kind: child_rows.get(kind, [])
            )
            if fault == "missing_clock":
                child_rows["host-clock"] = []
            if fault == "duplicate_clock":
                child_rows["host-clock"] *= 2
            if fault == "different_clock":
                child_rows["host-clock"] = [{"body": {"boot_id": "different"}}]
            if fault == "invalid_clock":
                child_rows["host-clock"] = [{"body": {}}]
                parent_rows["host-clock"] = [{"body": {}}]
            if fault == "reservation":
                parent_rows["reserved"] = []
            if fault == "path":
                right.root = tmp_path / "replacement"
            if fault == "plan":
                right.plan = {**child.plan, "unrecorded": True}
            if fault is None:
                assert verify_round_records(left, right) == binding
            else:
                with pytest.raises(ValueError, match=r"clock|reservation|binding"):
                    verify_round_records(left, right)


def test_bounded_journal_snapshot_rejects_utf8_bytes_before_decode(tmp_path, monkeypatch):
    from scripts.execution_capacity import observers
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError

    tmp_path.chmod(0o700)
    with observers.RecoveryJournal(tmp_path) as journal:
        journal.db.execute(
            "INSERT INTO intents(kind,identity,body) VALUES(?,?,?)",
            ("lease", "id", '"' + "海" * 40 + '"'),
        )
        journal.db.commit()
        monkeypatch.setattr(
            observers.json, "loads", lambda *args, **kwargs: pytest.fail("oversize row decoded")
        )
        assert hasattr(journal, "bounded_records"), "snapshot still uses eager journal reader"
        with pytest.raises(EvidenceQuotaError):
            list(journal.bounded_records("lease", EvidenceBudget(row_limit=100)))


def test_bounded_cumulative_snapshot_retains_all_families_and_enforces_total(tmp_path):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
    from scripts.execution_capacity.final_inventory import CumulativeJournal
    from scripts.execution_capacity.observers import RecoveryJournal

    roots = [tmp_path / str(i) for i in range(2)]
    for root in roots:
        root.mkdir(mode=0o700)
    with RecoveryJournal(roots[0]) as prior, RecoveryJournal(roots[1]) as current:
        prior.intent("lease", "old", {"namespace": "original"})
        current.intent("lease_state", "old:1", {"revision": 1})
        journal = CumulativeJournal([prior], current)
        assert hasattr(journal, "bounded_records"), "cumulative snapshots still eager"
        budget = EvidenceBudget()
        leases = dict(journal.bounded_records("lease", budget))
        states = dict(journal.bounded_records("lease_state", budget))
        assert list(leases) == ["old"]
        assert list(states) == ["old:1"]
        assert list(journal.bounded_records("environment_observation", budget)) == []
        assert list(journal.bounded_records("environment_read", budget)) == []
        assert budget.rows == 2
        tiny = EvidenceBudget(bytes_limit=30 * 65)
        assert list(journal.bounded_records("lease", tiny))
        with pytest.raises(EvidenceQuotaError):
            list(journal.bounded_records("lease_state", tiny))


@pytest.mark.parametrize(
    ("identity", "body", "receipt"),
    [
        (None, '"' + "x" * 1024 + '"', None),
        (None, "{}", None),
        ("", "{}", None),
        (b"identity", "{}", None),
        ("bad\0identity", "{}", None),
        ("id", b"{}", None),
        ("id", "{}", b"{}"),
        ("id", "{}", ""),
    ],
    ids=[
        "null-oversize",
        "null-small",
        "empty-id",
        "blob-id",
        "nul-id",
        "blob-body",
        "blob-receipt",
        "empty-receipt",
    ],
)
def test_invalid_journal_storage_never_selects_original_payload(tmp_path, identity, body, receipt):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.observers import RecoveryJournal

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("lease", "valid-row", {"retained": True})
        journal.db.execute(
            "INSERT INTO intents(kind,identity,body,receipt) VALUES(?,?,?,?)",
            ("lease", identity, body, receipt),
        )
        journal.db.commit()
        statements = []
        journal.db.set_trace_callback(statements.append)
        with pytest.raises(ValueError, match="invalid private journal storage population"):
            list(journal.bounded_records("lease", EvidenceBudget(row_limit=100)))
        assert not any(sql.startswith("SELECT identity,body,receipt") for sql in statements)


def test_bounded_journal_valid_receipt_and_empty_population(tmp_path):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.observers import RecoveryJournal

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("lease", "海", {"original": "value"})
        journal.acknowledge("lease", "海", {"observed": "done"})
        budget = EvidenceBudget()
        assert list(journal.bounded_records("empty", budget)) == []
        assert (budget.rows, budget.bytes) == (0, 0)
        assert dict(journal.bounded_records("lease", budget)) == {
            "海": {"body": {"original": "value"}, "receipt": {"observed": "done"}}
        }
        assert budget.rows == 1
