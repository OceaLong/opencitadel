"""Finite cohorts reproduce the complete original sorting and public validators."""

import pytest
from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_models import SourceOrigin
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.inventory import SourceInventory, make_cohorts
from scripts.execution_capacity.original_plain import canonical_original_digest
from scripts.execution_capacity.source_outputs import SourceOutputs


def test_owned_cohorts_match_old_sort_counts_duplicate_and_parity(tmp_path):
    membership = {
        "z": ("standard", "scope", "parent"),
        "a": ("standard", "scope", "parent"),
        "é": ("live", "scope", "other"),
    }
    origin = SourceOrigin(kind="base", seal_id="seal", round=None, boot_id=None, clone_id=None)
    origins = {"parent": origin, "other": origin}
    runs = [
        {"run_id": key, "formal_events": n, "observations": n + 1, "visible_steps": n + 2}
        for n, key in enumerate(("z", "a", "z", "é"))
    ]
    old = [row.model_dump() for row in make_cohorts(membership, runs, origins)]
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        result = SourceInventory()
        outputs = SourceOutputs(result, evidence.journal, parent=evidence._cleanup_token)
        result.runs.extend(runs)
        outputs.close_data()
        result.cohorts = make_cohorts(membership, result.runs, origins, outputs=outputs)
        outputs.close()
        assert canonical_original_digest(
            result.cohorts, owner=evidence.journal, budget=evidence.budget
        ) == canonical_digest(old)
        assert list(result.cohorts[1]["run_ids"]) == ["a", "z", "z"]
        from scripts.execution_capacity.cohort_inventory import cohorts_equal

        assert cohorts_equal(
            result.cohorts, old, budget=evidence.budget, left_owner=evidence.journal
        )
        replayed = SourceInventory()
        replay = SourceOutputs(replayed, evidence.journal, expected=vars(result))
        replayed.runs.extend(runs)
        replay.close_data()
        replayed.cohorts = make_cohorts(membership, replayed.runs, origins, outputs=replay)
        replay.close()
        assert replayed.cohorts is result.cohorts


@pytest.mark.parametrize("bad_id", ["", "x" * 256, 1, True])
def test_owned_cohort_keeps_public_run_id_validation(tmp_path, bad_id):
    origin = SourceOrigin(kind="base", seal_id="seal", round=None, boot_id=None, clone_id=None)
    membership = {bad_id: ("standard", "scope", "parent")}
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        result = SourceInventory()
        outputs = SourceOutputs(result, evidence.journal, parent=evidence._cleanup_token)
        result.runs.append(
            {"run_id": bad_id, "formal_events": 1, "observations": 2, "visible_steps": 3}
        )
        outputs.close_data()
        with pytest.raises(ValueError, match=r"string|validation"):
            make_cohorts(membership, result.runs, {"parent": origin}, outputs=outputs)


@pytest.mark.parametrize("fault", ["kind", "scope", "origin", "count"])
def test_private_control_validation_matches_public_oracle(tmp_path, fault):
    from pydantic import ValidationError

    membership = {"r": ("standard", "scope", "parent")}
    origin = {"kind": "base", "seal_id": "seal", "round": None, "boot_id": None, "clone_id": None}
    runs = [{"run_id": "r", "formal_events": 1, "observations": 2, "visible_steps": 3}]
    if fault == "kind":
        membership["r"] = ("invalid", "scope", "parent")
    elif fault == "scope":
        membership["r"] = ("standard", "", "parent")
    elif fault == "origin":
        origin["boot_id"] = "foreign"
    else:
        runs[0]["formal_events"] = -1
    with pytest.raises(ValidationError):
        make_cohorts(membership, runs, {"parent": origin})
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        result = SourceInventory()
        outputs = SourceOutputs(result, evidence.journal, parent=evidence._cleanup_token)
        result.runs.extend(runs)
        outputs.close_data()
        with pytest.raises(ValidationError):
            make_cohorts(membership, result.runs, {"parent": origin}, outputs=outputs)


def test_public_record_admits_whole_record_before_decode(tmp_path, monkeypatch):
    import json

    from scripts.execution_capacity import public_record
    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        evidence.begin_cleanup()
        writer = evidence.journal.begin_collection(evidence._cleanup_token, "cohorts")
        writer.append({"origin": {"kind": "base"}, "run_ids": ["first"]})
        writer.append({"origin": {"kind": "round"}, "run_ids": ["second"]})
        value = {"cohorts": writer.complete(), "extra": "whole-record"}
        expected = {"cohorts": [value["cohorts"][1]], "extra": "whole-record"}
        size = len(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode())
        monkeypatch.setattr(public_record, "MAX_ARTIFACT_BYTES", size)
        assert (
            public_record.public_record_value(
                value, owner=evidence.journal, budget=evidence.budget, cohort_kind="round"
            )
            == expected
        )
        monkeypatch.setattr(public_record, "MAX_ARTIFACT_BYTES", size - 1)

        def unexpected_decode(value):
            raise AssertionError("decoded before whole record admission")

        monkeypatch.setattr(public_record, "strict_json", unexpected_decode)
        with pytest.raises(EvidenceQuotaError, match="complete public record"):
            public_record.public_record_value(
                value, owner=evidence.journal, budget=evidence.budget, cohort_kind="round"
            )
