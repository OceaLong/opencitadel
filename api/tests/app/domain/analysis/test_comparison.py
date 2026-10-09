"""Alignment evidence must not turn equal labels or concurrent order into facts."""

from dataclasses import replace
from importlib.util import find_spec

import pytest


def comparison():
    assert find_spec("app.domain.analysis.comparison") is not None, "comparison behavior is missing"
    from app.domain.analysis import comparison as module

    return module


def test_repeated_keys_are_not_auto_matched():
    c = comparison()
    assert c.pair_status("tool:a", "tool:a", False) == "unmatched"
    assert c.pair_status("semantic:1", "semantic:1", True) == "suggested"


def steps(c):
    return (
        c.StepIdentity(
            run_id="left",
            step_id="same-uuid",
            attempt_id="1",
            cut="cut1",
            kind="tool",
            tool_name="search",
            case_revision="case1",
            semantic_key="lookup",
            tool_contract_revision="contract1",
            semantic_source="fixed_case",
        ),
        c.StepIdentity(
            run_id="right",
            step_id="same-uuid",
            attempt_id="1",
            cut="cut2",
            kind="tool",
            tool_name="search",
            case_revision="case1",
            semantic_key="lookup",
            tool_contract_revision="contract1",
            semantic_source="fixed_case",
        ),
    )


def test_semantic_match_requires_case_contract_and_trusted_provenance():
    c = comparison()
    left, right = steps(c)
    match = c.suggest_alignments([left], [right])[0]
    assert (match.status, match.provenance) == ("suggested", "fixed_case_semantic")
    for changes in (
        {"case_revision": "case2"},
        {"tool_contract_revision": "contract2"},
        {"semantic_source": None},
        {"semantic_key": None},
    ):
        assert c.suggest_alignments([left], [replace(right, **changes)])[0].status == "unmatched"


def test_repeated_parallel_semantics_do_not_use_uuid_or_order_to_break_ambiguity():
    c = comparison()
    left, right = steps(c)
    duplicate = replace(right, step_id="other", local_order=2)
    assert c.suggest_alignments([left], [duplicate, right])[0].status == "unmatched"
    assert c.suggest_alignments([left], [right, duplicate])[0].status == "unmatched"


def test_only_identical_run_step_attempt_cut_is_direct_reference():
    c = comparison()
    left, right = steps(c)
    assert c.suggest_alignments([left], [left])[0].status == "explicit"
    assert c.suggest_alignments([left], [right])[0].status != "explicit"
    assert c.suggest_alignments([left], [replace(left, cut="later")])[0].status != "explicit"


def test_unique_serial_path_is_a_reasoned_suggestion_but_parallel_path_is_not():
    c = comparison()
    left, right = steps(c)
    left = replace(
        left, semantic_key=None, semantic_source=None, parent_path=("root",), local_order=1
    )
    right = replace(
        right, semantic_key=None, semantic_source=None, parent_path=("root",), local_order=1
    )
    assert c.suggest_alignments([left], [right])[0].provenance == "serial_path_heuristic"
    assert c.suggest_alignments([left], [replace(right, parallel=True)])[0].status == "unmatched"


def test_detail_cap_checked_before_any_pair_work():
    c = comparison()
    with pytest.raises(ValueError, match="detail_limit"):
        c.validate_detail_runs([str(i) for i in range(6)])
    assert c.validate_detail_runs(["a", "a", "b"]) == ("a", "b")


def test_retained_adapter_uses_verified_ledger_evidence_not_matching_public_dto_fields():
    c = comparison()
    assert hasattr(c, "retained_step_identities"), "retained trusted evidence adapter missing"
    step = {
        "run_id": "r",
        "step_id": "s",
        "attempt_id": "a",
        "kind": "tool",
        "tool_name": "search",
        "semantic_key": "forged",
        "tool_contract_revision": "contract",
    }
    plain = c.retained_step_identities({"steps": [step]}, "cut")[0]
    assert plain.semantic_key is None
    trusted = c.retained_step_identities(
        {
            "steps": [step],
            "semantic_evidence": [
                {
                    "step_id": "s",
                    "attempt_id": "a",
                    "case_revision": "case",
                    "semantic_key": "slot",
                    "tool_contract_revision": "contract",
                    "semantic_source": "fixed_case",
                }
            ],
        },
        "cut",
    )[0]
    assert (trusted.semantic_key, trusted.case_revision) == ("slot", "case")
    duplicate = {
        "steps": [step],
        "semantic_evidence": [
            {
                "step_id": "s",
                "attempt_id": "a",
                "case_revision": "case",
                "semantic_key": key,
                "tool_contract_revision": "contract",
                "semantic_source": "fixed_case",
            }
            for key in ("x", "y")
        ],
    }
    assert c.retained_step_identities(duplicate, "cut")[0].semantic_key is None


def test_manual_confirmation_and_unpair_override_only_the_target_run_pair():
    c = comparison()
    assert hasattr(c, "apply_manual_alignments"), "manual alignment projection missing"
    left, right = steps(c)
    other = replace(right, run_id="third")
    from dataclasses import asdict

    suggestions = [
        dict(
            asdict(c.suggest_alignments([left], [candidate])[0]),
            run_pair=["left", candidate.run_id],
        )
        for candidate in (right, other)
    ]
    record = {
        "revision": 2,
        "supersedes": 1,
        "author": "reviewer",
        "created_at": "now",
        "edit": {
            "left_run_id": "left",
            "left_step_id": "same-uuid",
            "right_run_id": "right",
            "right_step_id": "same-uuid",
            "action": "confirm",
        },
    }
    result = c.apply_manual_alignments(suggestions, [record], [left, right, other])
    assert [row["status"] for row in result if row["run_pair"] == ["left", "right"]] == [
        "confirmed"
    ]
    assert [row["status"] for row in result if row["run_pair"] == ["left", "third"]] == [
        "suggested"
    ]
    assert next(row for row in result if row["status"] == "confirmed")["author"] == "reviewer"
    record["edit"]["action"] = "unpair"
    result = c.apply_manual_alignments(suggestions, [record], [left, right, other])
    assert (
        next(row for row in result if row["run_pair"] == ["left", "right"])["status"] == "unmatched"
    )


def test_recorded_activity_proof_does_not_claim_attempt_consumption():
    from app.domain.analysis.comparison import retained_step_identities, suggest_alignments

    def body(run, attempt):
        return {
            "steps": [
                {
                    "run_id": run,
                    "step_id": "step",
                    "attempt_id": attempt,
                    "kind": "tool",
                    "tool_name": "tool",
                }
            ],
            "semantic_evidence": [
                {
                    "step_id": "step",
                    "attempt_id": attempt,
                    "case_revision": "case",
                    "semantic_key": "recording-slot:version:slot",
                    "tool_contract_revision": "digest",
                    "semantic_source": "fixed_case",
                    "provenance_scope": "activity",
                }
            ],
        }

    left = retained_step_identities(body("left", "later-attempt"), "cut")
    right = retained_step_identities(body("right", "other-attempt"), "cut")
    match = suggest_alignments(left, right)[0]
    assert match.status == "suggested"
    assert match.left.provenance_scope == "activity"
    assert "activity" in match.reason.lower()
    assert "attempt consumption" in match.reason.lower()


def test_manual_projection_keeps_the_selected_attempt_identity():
    c = comparison()
    left, right = steps(c)
    later = replace(left, attempt_id="2")
    record = {
        "revision": 1,
        "supersedes": 0,
        "author": "user",
        "created_at": "now",
        "edit": {
            "left_run_id": left.run_id,
            "left_step_id": left.step_id,
            "left_attempt_id": "1",
            "right_run_id": right.run_id,
            "right_step_id": right.step_id,
            "right_attempt_id": "1",
            "action": "confirm",
        },
    }
    result = c.apply_manual_alignments([], [record], [left, later, right])
    assert result[0]["left"]["attempt_id"] == "1"


def test_revoked_explicit_selection_never_enables_automatic_pairs():
    from app.domain.analysis.score_summary import score_summary

    records = [
        {
            "family": "agent",
            "dataset_version": "dataset",
            "mode": "recorded",
            "environment_version": "recording",
            "rubric": "rubric",
            "evaluation_revision": 1,
            "result_id": config,
            "case_id": "case",
            "config_id": config,
            "execution_status": "succeeded",
            "required_conditions": [],
            "source_sets": [
                {
                    "source": "model",
                    "required_dimensions": [],
                    "applicable_dimensions": ["quality"],
                    "source_set_id": config,
                }
            ],
            "scores": [
                {
                    "source": "model",
                    "dimension": "quality",
                    "value": 3,
                    "status": "valid",
                    "invalidated": False,
                    "source_set_id": config,
                }
            ],
        }
        for config in ("visible-B", "visible-C")
    ]
    assert score_summary(records)["comparisons"]
    output = score_summary(records, selection=(), allow_automatic=False)
    assert output["comparisons"] == []
    assert output["selection_status"] == "unavailable"
