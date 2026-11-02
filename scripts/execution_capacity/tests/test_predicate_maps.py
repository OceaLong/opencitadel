"""Derived predicate scratch maps retain exact values and bounded key order."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner


def test_predicate_map_keeps_first_key_last_value_and_exact_types(tmp_path):
    from scripts.execution_capacity.predicate_maps import PredicateMap

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        values = PredicateMap(evidence.journal, "states")
        values["b"] = {"receipt": True}
        values["a"] = {"receipt": None}
        values["b"] = {"receipt": 1}
        assert list(values.items()) == [("b", {"receipt": 1}), ("a", {"receipt": None})]
        assert len(values) == 2
        assert values.get("missing") is None
        assert values.same_keys({"a": 0, "b": 1})
        assert not values.same_keys({"a": 0})
        assert values.same_values({"a": {"receipt": None}, "b": {"receipt": 1}})
        assert not values.same_values({"a": {"receipt": None}, "b": {"receipt": True}})
    with pytest.raises(ValueError, match=r"closed|invalid"):
        values.get("b")


def test_predicate_map_rejects_oversized_row_before_index_insert(tmp_path):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.predicate_maps import PredicateMap

    with EvidenceOwner(
        budget=EvidenceBudget(bytes_limit=1024 * 1024, rows_limit=10000, row_limit=4096),
        original_root=tmp_path / "originals",
        index_bytes=64 * 1024,
    ) as evidence:
        values = PredicateMap(evidence.journal, "observations")
        with pytest.raises(ValueError, match=r"quota|bound"):
            values["large"] = {"value": "x" * 10000}
        assert len(values) == 0


def test_predicate_comparison_rejects_foreign_empty_map_and_arbitrary_mapping(tmp_path):
    from collections import UserDict

    from scripts.execution_capacity.predicate_maps import PredicateMap

    with (
        EvidenceOwner(original_root=tmp_path / "a", index_bytes=128 * 1024) as left,
        EvidenceOwner(original_root=tmp_path / "b", index_bytes=128 * 1024) as right,
    ):
        values = PredicateMap(left.journal, "states")
        right.begin_cleanup()
        foreign = right.journal.begin_dictionary(right._cleanup_token, "foreign").complete()
        for other in (foreign, UserDict()):
            with pytest.raises(ValueError, match=r"owner|closed"):
                values.same_values(other)
