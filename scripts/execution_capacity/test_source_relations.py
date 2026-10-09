"""Actual source membership keeps exact duplicate/scope/parent predicates."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.inventory import batch_membership


def test_indexed_source_membership_matches_old_values_and_sorted_order(tmp_path):
    attempts = [
        {
            "batch_id": "batch",
            "scope_key": "scope",
            "run_id": run,
            "result_id": run,
            "attempt": 1,
            "intent": "committed",
        }
        for run in ("z", "a")
    ]
    judges = [
        {
            "batch_id": "batch",
            "scope_key": "scope",
            "run_id": "m",
            "result_id": "result",
            "id": "judge",
        }
    ]
    old = batch_membership(attempts, judges, {"batch": "scope"})
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        indexed = batch_membership(attempts, judges, {"batch": "scope"}, owner=evidence.journal)
        assert [(key, tuple(value)) for key, value in indexed.items()] == list(old.items())
        assert [(key, tuple(value)) for key, value in indexed.sorted_items()] == sorted(old.items())
        assert [tuple(value) for value in indexed.values()] == list(old.values())


@pytest.mark.parametrize("fault", ["run", "parent", "scope", "missing-intent"])
def test_indexed_source_membership_preserves_all_original_rejections(tmp_path, fault):
    first = {
        "batch_id": "batch",
        "scope_key": "scope",
        "run_id": "a",
        "result_id": "result-a",
        "attempt": 1,
        "intent": "committed",
    }
    second = {**first, "run_id": "b", "result_id": "result-b"}
    if fault == "run":
        second["run_id"] = "a"
    elif fault == "parent":
        second["result_id"] = "result-a"
    elif fault == "scope":
        second["scope_key"] = "other"
    else:
        second["intent"] = None
    with (
        EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence,
        pytest.raises(ValueError, match="missing/duplicate/foreign"),
    ):
        batch_membership([first, second], [], {"batch": "scope"}, owner=evidence.journal)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (True, 1),
        (1, 1.0),
        (-0.0, 0),
        (2**80 + 1, float(2**80)),
        (2**80, float(2**80)),
        (2**80 + 1, 2**80 + 2),
    ],
)
def test_legacy_attempt_numeric_key_matches_original_python_set(tmp_path, left, right):
    from scripts.execution_capacity.replay_relations import legacy_attempt_key

    attempts = [
        {
            "batch_id": "b",
            "scope_key": "s",
            "run_id": run,
            "result_id": "result",
            "attempt": attempt,
            "intent": "yes",
        }
        for run, attempt in (("a", left), ("b", right))
    ]
    same = len({left, right}) == 1
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        for kind, prefix in [("parent", ["evaluation_subject", "result"]), ("run-attempt", ["r"])]:
            encoded = [
                legacy_attempt_key(
                    kind, [*prefix, value], owner=evidence.journal, budget=evidence.budget
                )
                for value in (left, right)
            ]
            assert (encoded[0] == encoded[1]) is same
        if same:
            for owner in (None, evidence.journal):
                with pytest.raises(ValueError, match="missing/duplicate/foreign"):
                    batch_membership(attempts, [], {"b": "s"}, owner=owner)
        else:
            old = batch_membership(attempts, [], {"b": "s"})
            new = batch_membership(attempts, [], {"b": "s"}, owner=evidence.journal)
            assert [(key, tuple(value)) for key, value in new.items()] == list(old.items())


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (["b", "a"], ["a", "b"]),
        (["a", "a"], ["a"]),
        (["a"], ["b"]),
        (["a"], ["a", "b"]),
        (["\ud800", "🙂", ""], ["🙂", "", "\ud800"]),
    ],
)
def test_indexed_owned_set_preserves_exact_old_digest_and_rejections(tmp_path, actual, expected):
    from scripts.execution_capacity.inventory import exact_owned_set

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        if len(set(actual)) != len(actual) or set(actual) != set(expected):
            with pytest.raises(ValueError, match="complete actual owned"):
                exact_owned_set(iter(actual), iter(expected), owner=evidence.journal)
        else:
            assert exact_owned_set(
                iter(actual), iter(expected), owner=evidence.journal
            ) == exact_owned_set(actual, expected)


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "wrong-scope", "lag"])
def test_indexed_projectors_check_exact_scopes_and_original_metadata(tmp_path, fault):
    from scripts.execution_capacity.inventory import validate_projectors
    from scripts.execution_capacity.predicate_maps import PredicateMap

    rows = [
        {
            "scope": "scope",
            "head": 8,
            "checkpoint": 8,
            "generation": "live",
            "source_version": 1,
            "algorithm_version": 1,
        }
    ]
    if fault == "missing":
        rows = []
    elif fault == "duplicate":
        rows *= 2
    elif fault == "wrong-scope":
        rows[0]["scope"] = "other"
    elif fault == "lag":
        rows[0]["checkpoint"] = 7
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        scopes = PredicateMap(evidence.journal, "source_scopes")
        scopes["scope"] = None
        if fault:
            with pytest.raises(ValueError, match=r"projector|incompatible"):
                validate_projectors(rows, scopes, owner=evidence.journal)
        else:
            validate_projectors(rows, scopes, owner=evidence.journal)
