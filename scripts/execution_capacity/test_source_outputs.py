"""Source result producers and replay preserve complete per-family order."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.inventory import SourceInventory


@pytest.mark.parametrize("fault", [None, "extra", "missing", "late"])
def test_source_outputs_replay_complete_rows_before_returning_original(tmp_path, fault):
    from scripts.execution_capacity.source_outputs import SourceOutputs

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        result = SourceInventory()
        writer = SourceOutputs(result, evidence.journal, parent=evidence._cleanup_token)
        result.runs.append({"run_id": "a"})
        result.runs.append({"run_id": "b"})
        result.objects.extend([{"key": "object"}])
        writer.close_data()
        result.errors.append({"error": "late"})
        writer.close()
        expected = vars(result)
        actual = SourceInventory()
        replay = SourceOutputs(actual, evidence.journal, expected=expected)
        actual.runs.append({"run_id": "a"})
        if fault == "extra":
            actual.runs.append({"run_id": "b"})
            with pytest.raises(ValueError, match="source output"):
                actual.runs.append({"run_id": "extra"})
            return
        if fault == "missing":
            with pytest.raises(ValueError, match="source output"):
                replay.close_data()
            return
        if fault == "late":
            with pytest.raises(ValueError, match="source output"):
                actual.runs.append({"run_id": "changed"})
            return
        actual.runs.append({"run_id": "b"})
        actual.objects.append({"key": "object"})
        replay.close_data()
        actual.errors.append({"error": "late"})
        replay.close()
        assert actual.runs is expected["runs"]
        assert actual.errors is expected["errors"]


def test_finite_source_safe_digest_preserves_original_plain_json_bytes(tmp_path):
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.source_outputs import SourceOutputs

    plain = SourceInventory(
        database={
            "database_name": "db",
            "reads": [
                {
                    "name": "read",
                    "start_ns": 1,
                    "end_ns": 2,
                    "rows": 0,
                    "parameters": {"private": "omitted"},
                }
            ],
        },
        build={"files": {"unicode-é": "hash"}},
        reads_complete=False,
    )
    plain.runs = [{"run_id": "r", "formal_events": 1}]
    plain.errors = [{"stage": "read", "error": "late"}]
    old = canonical_digest(plain.safe())
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        result = SourceInventory(database=plain.database, build=plain.build)
        outputs = SourceOutputs(result, evidence.journal, parent=evidence._cleanup_token)
        result.runs.extend(plain.runs)
        result.errors.extend(plain.errors)
        reads = evidence.journal.begin_collection(evidence._cleanup_token, "query-reads")
        for row in plain.database["reads"]:
            reads.append(row)
        result.database = {**plain.database, "reads": reads.complete()}
        outputs.close_reads(result.database["reads"])
        outputs.close()
        assert result.safe_digest(owner=evidence.journal, budget=evidence.budget) == old


@pytest.mark.parametrize("fault", [None, "missing", "extra", "order", "late"])
def test_safe_reads_recompute_all_public_fields_without_parameters(tmp_path, fault):
    from scripts.execution_capacity.source_outputs import SourceOutputs

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        rows = [
            {"name": "a", "start_ns": 1, "parameters": {"secret": "excluded"}, "rows": 2},
            {"name": "b", "start_ns": 2, "preflight": {"private": "excluded"}, "error": "OSError"},
        ]

        def originals(slot, values):
            writer = evidence.journal.begin_collection(evidence._cleanup_token, slot)
            for row in values:
                writer.append(row)
            return writer.complete()

        result = SourceInventory(database={"reads": originals("reads", rows)})
        output = SourceOutputs(result, evidence.journal, parent=evidence._cleanup_token)
        output.close_reads(result.database["reads"])
        output.close()
        assert list(result.safe_reads) == [
            {"name": "a", "start_ns": 1, "rows": 2},
            {"name": "b", "start_ns": 2, "error": "OSError"},
        ]
        actual_rows = rows
        if fault == "missing":
            actual_rows = rows[:1]
        elif fault == "extra":
            actual_rows = [*rows, rows[0]]
        elif fault == "order":
            actual_rows = rows[::-1]
        elif fault == "late":
            actual_rows = [rows[0], {**rows[1], "error": "Changed"}]
        replayed = SourceInventory(database={"reads": originals("replayed-reads", actual_rows)})
        replay = SourceOutputs(replayed, evidence.journal, expected=vars(result))
        if fault is not None:
            with pytest.raises(ValueError, match="source output"):
                replay.close_reads(replayed.database["reads"])
        else:
            replay.close_reads(replayed.database["reads"])
            replay.close()
            assert replayed.safe_reads is result.safe_reads
