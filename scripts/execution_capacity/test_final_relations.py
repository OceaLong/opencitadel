"""Fixed final joins retain old dict overwrite and insertion-order semantics."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner


def test_final_relations_keep_all_occurrences_and_first_key_last_value(tmp_path):
    from scripts.execution_capacity.final_relations import FinalRelations

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        owner = evidence.journal
        writer = owner.begin_collection(evidence._cleanup_token, "dispatches")
        rows = [
            {"scope_key": "s", "call_identity": "b", "run_id": "old"},
            {"scope_key": "s", "call_identity": "a", "run_id": "a"},
            {"scope_key": "s", "call_identity": "b", "run_id": "new"},
        ]
        for row in rows:
            writer.append(row)
        values = writer.complete()
        relations = FinalRelations(owner, {"dispatches": values})
        assert list(relations.items("dispatches")) == list(
            {(r["scope_key"], r["call_identity"]): r for r in rows}.items()
        )
        assert list(relations.all("dispatches", ("s", "b"))) == [rows[0], rows[2]]
        assert relations.get("dispatches", ("s", "b")) == rows[2]
        assert relations.get("dispatches", ("s", "missing")) is None
    with pytest.raises(ValueError, match=r"closed|invalid"):
        relations.get("dispatches", ("s", "b"))


def test_final_upload_relation_preserves_duplicate_link_candidates(tmp_path):
    from scripts.execution_capacity.final_relations import FinalRelations

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        writer = evidence.journal.begin_dictionary(evidence._cleanup_token, "uploads")
        for key, port in [("first", "p"), ("unlinked", None), ("second", "p")]:
            writer.append(key, {"body": {"port_upload_id": port, "key": key}, "receipt": None})
        values = writer.complete()
        relations = FinalRelations(evidence.journal, {"linked": values})
        assert [row["key"] for row in relations.all("linked", "p")] == ["first", "second"]
        assert list(relations.all("linked", None)) == []
        assert relations.occurrences("linked") == 3


@pytest.mark.parametrize("duplicate_upload", [False, True])
def test_retained_history_indexed_joins_match_original_oracle(tmp_path, duplicate_upload):
    from types import SimpleNamespace

    from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
    from scripts.execution_capacity.final_inventory import retained_history
    from scripts.execution_capacity.replay_relations import same_value
    from scripts.execution_capacity.retained_final import RetainedHistory

    records = {kind: {} for kind in (*HISTORY_FAMILIES, *PREDICATE_FAMILIES)}
    records["object"]["k"] = {"body": {"size": 2, "sha256": "hash"}, "receipt": True}
    records["upload"]["p"] = {"body": {"key": "k", "size": 2, "sha256": "hash"}, "receipt": True}
    records["live_disposition"]["l"] = {
        "body": {
            "runs": ["r"],
            "dispatches": [
                {
                    "run_id": "r",
                    "call_identity": "call",
                    "state": "settled",
                    "settlement": 2,
                    "fact": 2,
                }
            ],
            "outcomes": {"r": "completed"},
        },
        "receipt": None,
    }
    uploads = {
        "u": {
            "body": {"port_upload_id": "p", "key": "k", "size": 2, "sha256": "hash"},
            "receipt": True,
        }
    }
    if duplicate_upload:
        uploads["v"] = uploads["u"]
    rows = {
        "execution_model_dispatches": [
            {"scope_key": "s", "call_identity": "call", "run_id": "old"},
            {"scope_key": "s", "call_identity": "call", "run_id": "r"},
        ],
        "evaluation_budget_reservations": [
            {"scope_key": "s", "call_identity": "call", "state": "settled", "settlement": 1},
            {"scope_key": "s", "call_identity": "call", "state": "settled", "settlement": 2},
        ],
        "execution_model_settlements": [{"scope_key": "s", "call_identity": "call", "fact": 2}],
        "execution_run_projection": [
            {"run_id": "r", "status": "running"},
            {"run_id": "r", "status": "completed"},
        ],
    }
    objects = [
        {"key": "k", "size_bytes": 1, "sha256": "old"},
        {"key": "k", "size_bytes": 2, "sha256": "hash"},
    ]
    owners = [
        {"stream_id": "r", "owner_scope_key": "old"},
        {"stream_id": "r", "owner_scope_key": "s"},
    ]
    oracle = retained_history(
        SimpleNamespace(records=lambda kind: records[kind].items()),
        uploads,
        rows,
        SimpleNamespace(objects=objects, owners=owners),
    )
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()

        def finite(value, name):
            if type(value) is dict:
                writer = evidence.journal.begin_dictionary(evidence._cleanup_token, name)
                for key, row in value.items():
                    writer.append(key, row)
            else:
                writer = evidence.journal.begin_collection(evidence._cleanup_token, name)
                for row in value:
                    writer.append(row)
            return writer.complete()

        original = {kind: finite(value, kind) for kind, value in records.items()}
        history = RetainedHistory(original, budget=evidence.budget, owner=evidence.journal)
        actual = retained_history(
            history,
            finite(uploads, "uploads"),
            {name: finite(value, name) for name, value in rows.items()},
            SimpleNamespace(objects=finite(objects, "objects"), owners=finite(owners, "owners")),
            expected_issues=finite(oracle["issues"], "issues"),
        )
        assert same_value(actual, oracle, owner=evidence.journal, budget=evidence.budget)
        assert bool(actual["issues"]) is duplicate_upload


@pytest.mark.parametrize("fault", [None, "missing", "extra", "late", "order"])
def test_retained_issue_replay_consumes_exact_original_sequence(tmp_path, fault):
    from types import SimpleNamespace

    from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
    from scripts.execution_capacity.final_inventory import retained_history
    from scripts.execution_capacity.retained_final import RetainedHistory

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        records = {}
        for family in (*HISTORY_FAMILIES, *PREDICATE_FAMILIES):
            writer = evidence.journal.begin_dictionary(evidence._cleanup_token, family)
            records[family] = writer.complete()
        history = RetainedHistory(records, budget=evidence.budget, owner=evidence.journal)
        expected = [
            {"kind": "missing-port-upload", "identity": key, "state": "error"} for key in ("a", "b")
        ]
        if fault == "missing":
            expected.pop()
        elif fault == "extra":
            expected.append(expected[-1])
        elif fault == "late":
            expected[-1] = {**expected[-1], "identity": "changed"}
        elif fault == "order":
            expected.reverse()
        writer = evidence.journal.begin_collection(evidence._cleanup_token, "issues")
        for row in expected:
            writer.append(row)
        original = writer.complete()
        uploads = {key: {"body": {"port_upload_id": key}, "receipt": None} for key in ("a", "b")}

        def replay():
            return retained_history(
                history,
                uploads,
                {},
                SimpleNamespace(objects=[], owners=[]),
                expected_issues=original,
            )

        if fault:
            with pytest.raises(ValueError, match="original retained issue"):
                replay()
        else:
            assert replay()["issues"] is original


def test_disposition_calls_preserve_cross_scope_last_assignment(tmp_path):
    from scripts.execution_capacity.final_relations import FinalRelations

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        writer = evidence.journal.begin_collection(evidence._cleanup_token, "dispatches")
        rows = [
            {"scope_key": "s1", "call_identity": "same", "run_id": "r"},
            {"scope_key": "s1", "call_identity": "other", "run_id": "r"},
            {"scope_key": "s2", "call_identity": "same", "run_id": "r"},
            {"scope_key": "s3", "call_identity": "ignored", "run_id": "outside"},
        ]
        for row in rows:
            writer.append(row)
        relations = FinalRelations(evidence.journal, {"dispatches": writer.complete()})
        assert list(relations.calls({"r"})) == [
            ("same", (("s2", "same"), rows[2])),
            ("other", (("s1", "other"), rows[1])),
        ]


def test_final_relation_iteration_rechecks_original_first_identity(tmp_path):
    from scripts.execution_capacity.final_relations import FinalRelations

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        source = [{"key": "before", "size_bytes": 1, "sha256": "hash"}]
        relations = FinalRelations(evidence.journal, {"objects": source})
        source[0]["key"] = "after"
        with pytest.raises(ValueError, match="identity"):
            list(relations.items("objects"))


def test_predicate_source_groups_keep_full_attempt_and_projection_occurrences(tmp_path):
    from scripts.execution_capacity.final_relations import FinalRelations

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        attempts = [
            {
                "batch_id": "b",
                "case_revision_id": "c",
                "config_version_id": "v",
                "repetition": 0,
                "run_id": "r",
                "attempt": 1,
            }
        ]
        projection = [{"id": "lease", "revision": 1}, {"id": "lease", "revision": 2}]
        values = FinalRelations(
            evidence.journal, {"source_attempts": attempts, "lease_projection": projection}
        )
        assert list(values.all("source_attempts", ("b", "c", "v", 0))) == attempts
        assert list(values.all("source_attempts", ("b", "other", "v", 0))) == []
        assert list(values.all("lease_projection", "lease")) == projection
