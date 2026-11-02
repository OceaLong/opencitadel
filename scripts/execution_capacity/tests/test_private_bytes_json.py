"""Private compatibility JSON is never typed original authority."""

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError


def test_private_bytes_json_projection_is_explicit_and_precharged(monkeypatch):
    import base64
    import json

    from scripts.execution_capacity.evidence_json import chunks, json_digest
    from scripts.execution_capacity.safe_projection import typed_digest

    value = b"private original\x00\xff"
    marker = {"$bytes": base64.b64encode(value).decode("ascii")}
    budget = EvidenceBudget()
    assert json.loads(b"".join(chunks(value, budget=budget))) == marker
    assert json_digest(value, budget=budget) == json_digest(marker, budget=budget)
    assert typed_digest(value, budget=budget) != typed_digest(marker, budget=budget)

    def forbidden(*args):
        raise AssertionError("allocation before quota")

    monkeypatch.setattr(base64, "b64encode", forbidden)
    with pytest.raises(EvidenceQuotaError):
        list(chunks(b"123456789", budget=EvidenceBudget(bytes_limit=140)))


def test_owned_private_json_stream_preserves_original_expanded_wire(tmp_path):
    from datetime import date
    from decimal import Decimal
    from uuid import UUID

    from scripts.execution_capacity.evidence_json import chunks, json_digest
    from scripts.execution_capacity.original_journal import OriginalJournal

    expected = [
        {
            "binary": b"\x00\xff",
            "uuid": UUID(int=5),
            "decimal": Decimal("1.00"),
            "date": date(2020, 1, 2),
            "boolean": True,
            "int": 1,
            "nested": [None, "é"],
        }
    ]
    with OriginalJournal.create(
        tmp_path / "owner", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        token = owner.begin("operand:query-rows", {})
        writer = owner.begin_collection(token, "rows")
        writer.append(expected[0])
        rows = writer.complete()
        owner.complete(token, {"rows": rows})
        oracle = b"".join(chunks({"rows": expected}, budget=EvidenceBudget()))
        actual = b"".join(chunks({"rows": rows}, owner=owner, budget=owner.budget))
        assert actual == oracle
        assert json_digest({"rows": rows}, owner=owner, budget=owner.budget) == json_digest(
            {"rows": expected}, budget=EvidenceBudget()
        )
        with pytest.raises(ValueError, match="owner"):
            b"".join(chunks(rows, budget=EvidenceBudget()))
        with (
            OriginalJournal.create(
                tmp_path / "foreign", budget=EvidenceBudget(), index_bytes=512 * 1024
            ) as foreign,
            pytest.raises(ValueError, match=r"owner|foreign"),
        ):
            b"".join(chunks(rows, owner=foreign, budget=foreign.budget))
    with pytest.raises(ValueError, match="closed"):
        b"".join(chunks(rows, owner=owner, budget=EvidenceBudget()))


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ([b"[", b"]"], [b"[]"], True),
        ([b"", b"ab", b"c", b""], [b"a", b"", b"bc"], True),
        ([b"a", b"b"], [b"ab", b"c"], False),
        ([b"ab", b"c"], [b"a", b"bd"], False),
        ([], [b""], True),
    ],
)
def test_complete_byte_comparison_ignores_only_chunk_boundaries(left, right, expected):
    from scripts.execution_capacity.evidence_json import equal_streams

    assert equal_streams(iter(left), iter(right)) is expected


def test_complete_byte_comparison_consumes_late_failure():
    from scripts.execution_capacity.evidence_json import equal_streams

    def failed():
        yield b"[]"
        raise ValueError("late original failure")

    with pytest.raises(ValueError, match="late original"):
        equal_streams(iter([b"[", b"]"]), failed())
