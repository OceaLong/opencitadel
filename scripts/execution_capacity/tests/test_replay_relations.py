"""Fixed replay relations use only actual original owner input and private scratch."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.retained_source import OriginalTrace
from sqlalchemy import text


def trace_for(owner):
    return OriginalTrace(
        {
            "operands": owner.originals,
            "sql": owner.sql_reads,
            "objects": owner.journal.sequence("objects"),
        },
        owner=owner.journal,
        budget=owner.budget,
    )


def sql(snapshot, uow=1):
    return {
        "statement": "select 1",
        "parameters": {},
        "bound_parameters": {},
        "error": None,
        "dispatched": True,
        "start_ns": 1,
        "end_ns": 2,
        "uow": uow,
        "snapshot": snapshot,
        "preflight": {
            "snapshot": snapshot,
            "row_count": 0,
            "max_bytes": 0,
            "total_bytes": 0,
            "read_only": "on",
            "isolation": "repeatable read",
        },
    }


def parent(owner, operation, value, *, key="first"):
    rows = owner.originals["journal-read"]
    owner.journal.append(
        "operand:journal-read",
        {
            "sequence": len(rows) + 1,
            "operation": operation,
            "kind": "run",
            "key": key if operation == "get" else None,
            "error": None,
            "sql_boundary": 0,
            "value": value,
        },
    )


def test_actual_trace_snapshot_relation_is_session_local_and_closes(tmp_path):
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=128 * 1024) as owner:
        for value in (sql("first"), sql("first"), sql("second")):
            owner.journal.append("sql", value)
        trace = trace_for(owner)
        assert trace.dispatch(text("select 1"))["snapshot"] == "first"
        assert trace.dispatch(text("select 1"))["snapshot"] == "first"
        with pytest.raises(ValueError, match="snapshot"):
            trace.dispatch(text("select 1"))
        assert trace.relations.snapshot_count() == 1
        independent = trace_for(owner)
        independent.sql_position = 2
        assert independent.dispatch(text("select 1"))["snapshot"] == "second"
        assert independent.relations.snapshot_count() == 1
    with pytest.raises(ValueError, match=r"closed|invalid"):
        independent.relations.snapshot_count()


@pytest.mark.parametrize("fault", [None, "changed", "receipt-type", "absence"])
def test_parent_relation_reloads_complete_original_values(tmp_path, fault):
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=128 * 1024) as owner:
        first = {"body": {"b": [1, 2], "a": "same"}, "receipt": 1}
        second = {"receipt": 1, "body": {"a": "same", "b": [1, 2]}}
        if fault == "changed":
            second["body"]["b"][1] = 3
        elif fault == "receipt-type":
            second["receipt"] = True
        elif fault == "absence":
            first = None
        parent(owner, "get", first)
        parent(owner, "get", second)
        trace = trace_for(owner)
        assert trace.get("run", "first") == first
        if fault:
            with pytest.raises(ValueError, match="parent changed"):
                trace.get("run", "first")
        else:
            assert trace.get("run", "first") == second
        assert trace.relations.parent_count() == 1


@pytest.mark.parametrize("keys", [["a", "a"], ["b", "a"]])
def test_parent_enumeration_rejects_duplicate_or_descending_identity(tmp_path, keys):
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=128 * 1024) as owner:
        parent(owner, "records", [[key, {"body": {}, "receipt": None}] for key in keys])
        trace = trace_for(owner)
        with pytest.raises(ValueError, match="enumeration order"):
            trace.records("run")


def test_principal_relation_preserves_typed_value_and_session_isolation(tmp_path):
    from uuid import UUID

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=128 * 1024) as owner:
        trace = trace_for(owner)
        value = {"user_id": UUID(int=1), "roles": ["a", "b"], "active": True}
        trace.set_principal("user:first", value)
        value["roles"].append("caller-mutation")
        assert trace.principal("user:first") == {
            "user_id": UUID(int=1),
            "roles": ["a", "b"],
            "active": True,
        }
        assert trace.principal("missing") is None
        assert trace_for(owner).principal("user:first") is None


def test_parent_relation_compares_full_owned_finite_rows_with_late_difference(tmp_path):
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=256 * 1024) as owner:
        for last in (9, 9, 10):
            token = owner.journal.begin("operand:journal-read", {})
            writer = owner.journal.begin_collection(token, "body")
            for ordinal in range(10):
                writer.append({"ordinal": ordinal, "value": last if ordinal == 9 else ordinal})
            rows = writer.complete()
            owner.journal.complete(
                token,
                {
                    "sequence": len(owner.originals["journal-read"]),
                    "operation": "get",
                    "kind": "run",
                    "key": "first",
                    "error": None,
                    "sql_boundary": 0,
                    "value": {"body": {"rows": rows}, "receipt": None},
                },
            )
        trace = trace_for(owner)
        assert len(trace.get("run", "first")["body"]["rows"]) == 10
        assert len(trace.get("run", "first")["body"]["rows"]) == 10
        with pytest.raises(ValueError, match="parent changed"):
            trace.get("run", "first")
        assert trace.relations.parent_count() == 1


def test_relation_key_quota_is_charged_before_index_insert(tmp_path):
    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=128 * 1024) as owner:
        owner.journal.append("sql", sql("first"))
        trace = trace_for(owner)
        row = owner.sql_reads[0]
        owner.budget.reserve(
            owner.budget.bytes_limit - owner.budget.bytes - owner.budget.workspace_bytes - 1,
            rows=0,
        )
        with pytest.raises(EvidenceQuotaError):
            trace.relations.snapshot(0, row)
        assert trace.relations.snapshot_count() == 0


def test_finite_value_comparison_rejects_foreign_or_closed_owner(tmp_path):
    from scripts.execution_capacity.replay_relations import same_value

    with EvidenceOwner(original_root=tmp_path / "one", index_bytes=128 * 1024) as owner:
        with EvidenceOwner(original_root=tmp_path / "two", index_bytes=128 * 1024) as foreign:
            token = foreign.journal.begin("operand:journal-read", {})
            writer = foreign.journal.begin_collection(token, "body")
            writer.append(1)
            rows = writer.complete()
            foreign.journal.complete(token, {"rows": rows})
            with pytest.raises(ValueError, match="foreign"):
                same_value(rows, [1], owner=owner.journal, budget=owner.budget)
        with pytest.raises(ValueError, match=r"closed|invalid"):
            same_value(rows, [1], owner=foreign.journal, budget=foreign.budget)


def test_retained_history_uses_owned_index_order_and_rechecks_rows(tmp_path):
    from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
    from scripts.execution_capacity.retained_final import RetainedHistory

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=128 * 1024) as owner:
        records = {family: {} for family in (*HISTORY_FAMILIES, *PREDICATE_FAMILIES)}
        records["object"] = {
            key: {"body": {"key": key}, "receipt": None} for key in ("é", "a", "🙂")
        }
        history = RetainedHistory(records, budget=owner.budget, owner=owner.journal)
        rows = history.records("object")
        assert not isinstance(rows, list)
        assert [key for key, _ in rows] == ["a", "é", "🙂"]
        assert history.get("object", "a") == {"body": {"key": "a"}, "receipt": None}
        records["object"]["a"] = {"body": {}}
        with pytest.raises(ValueError, match="body/receipt"):
            list(history.records("object"))
    with pytest.raises(ValueError, match=r"closed|invalid"):
        history.get("object", "é")


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "receipt-type", "late-change"])
def test_lease_lookup_joins_complete_first_receipt_and_keeps_ambiguity(tmp_path, fault):
    from copy import deepcopy

    from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
    from scripts.execution_capacity.retained_final import RetainedHistory

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=256 * 1024) as owner:
        records = {family: {} for family in (*HISTORY_FAMILIES, *PREDICATE_FAMILIES)}
        first = {
            "start_ns": 1,
            "name": "database",
            "parameters": {"b": 2, "a": 1},
            "result_digest": "same",
        }
        body = {"batch_id": "batch", "reads": [deepcopy(first)], "untouched": [1, 2]}
        records["environment_read"]["selected"] = {"body": body, "receipt": None}
        records["environment_read"]["different-batch"] = {
            "body": {**body, "batch_id": "elsewhere"},
            "receipt": None,
        }
        if fault == "duplicate":
            records["environment_read"]["ambiguous"] = deepcopy(
                records["environment_read"]["selected"]
            )
        history = RetainedHistory(records, budget=owner.budget, owner=owner.journal)
        request = {
            "parameters": {"a": 1, "b": 2},
            "result_digest": "same",
            "name": "database",
            "start_ns": 1,
        }
        if fault == "missing":
            request["start_ns"] = 2
        elif fault == "receipt-type":
            request["start_ns"] = True
        elif fault == "late-change":
            body["reads"][0]["start_ns"] = 5
        if fault:
            with pytest.raises(ValueError, match=r"lease read|lease index"):
                history.lease_read("batch", request)
        else:
            key, row = history.lease_read("batch", request)
            assert key == "selected"
            assert row == {"body": body, "receipt": None}
