"""Pure inventory joins; no deployment, process or storage effects."""

from copy import deepcopy

import pytest
from scripts.acceptance import capacity_models as m
from scripts.acceptance import capacity_physical as p
from scripts.acceptance.capacity_io import canonical_digest

D = "d" * 64


def cohort(kind, ids, *, origin=None):
    counts = {
        "runs": len(ids),
        "formal_events": len(ids) * 3,
        "observations": 0,
        "visible_steps": 0,
    }
    return {
        "cohort_id": kind,
        "kind": kind,
        "scope_id": "scope",
        "run_ids": ids,
        "source": counts,
        "view": counts,
        "parity_digest": D,
        "origin": origin
        or {"kind": "base", "seal_id": "seal", "round": None, "boot_id": None, "clone_id": None},
    }


def fixture():
    base = [
        cohort("standard", ["history"]),
        cohort("step_capacity", ["probe"]),
        cohort("evaluation_subject", ["subject"]),
        cohort("evaluation_judge", ["judge"]),
    ]
    binding = {
        "parent_attempt_id": "attempt",
        "round_id": "round",
        "sample_id": "sample",
        "window_id": "window",
        "parent_plan_digest": D,
        "child_plan_digest": D,
        "reservation_digest": D,
        "schema_version": 1,
        "child_origin_sha256": "d" * 64,
    }
    origin = {
        "kind": "round",
        "seal_id": "seal",
        "round": binding,
        "boot_id": "boot",
        "clone_id": "round",
    }
    increment = cohort("live", ["new"], origin=origin)
    round_row = {
        "origin": origin,
        "base_inventory_digest": canonical_digest(base),
        "cohorts": [increment],
        "owned_run_ids": ["history", "probe", "subject", "judge", "new"],
        "errors": [],
    }
    return base, increment, round_row


def parse(base, increment, round_row):
    round_row = deepcopy(round_row)
    actual = round_row.pop("owned_run_ids")
    round_row.update(owned_run_count=len(actual), owned_run_digest=canonical_digest(sorted(actual)))
    return (
        [m.Cohort.model_validate(c) for c in base],
        m.Cohort.model_validate(increment),
        m.RoundInventory.model_validate(round_row),
    )


def test_immutable_base_plus_round_counts_each_logical_run_once():
    base, increment, row = parse(*fixture())
    actual = p.join_inventories("seal", base, [row], [*base, increment], "attempt")
    assert actual == {"runs": 5, "formal_events": 15, "observations": 0, "visible_steps": 0}


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "same_count", "duplicate", "base_changed", "relabel", "base_live"],
)
def test_actual_inventory_rejects_identity_and_origin_corruption(mutation):
    base, increment, row = fixture()
    if mutation == "missing":
        row["owned_run_ids"].remove("subject")
    if mutation == "extra":
        row["owned_run_ids"].append("foreign")
    if mutation == "same_count":
        row["owned_run_ids"][-1] = "wrong"
    if mutation == "duplicate":
        row["owned_run_ids"].append("new")
    if mutation == "base_changed":
        base[0]["run_ids"] = ["replaced"]
    if mutation == "relabel":
        row["origin"]["round"]["parent_attempt_id"] = "other"
    if mutation == "base_live":
        base.append(cohort("live", ["precreated"]))
    base, increment, row = parse(base, increment, row)
    with pytest.raises(ValueError, match=r"inventory|source|Run|origin|clone"):
        p.join_inventories("seal", base, [row], [*base, increment], "attempt")


def test_cloned_history_not_added_as_new_logical_runs():
    raw_base, raw_increment, raw_row = fixture()
    next_row = deepcopy(raw_row)
    next_row["origin"]["round"].update(round_id="round2", sample_id="sample2", window_id="window2")
    next_row["origin"].update(boot_id="boot2", clone_id="round2")
    next_row["cohorts"][0]["origin"] = deepcopy(next_row["origin"])
    next_row["cohorts"][0].update(cohort_id="live2", run_ids=["next"])
    next_row["owned_run_ids"][-1] = "next"
    base, increment, row = parse(raw_base, raw_increment, raw_row)
    other = parse(raw_base, raw_increment, next_row)[2]
    total = p.join_inventories(
        "seal", base, [row, other], [*base, increment, *other.cohorts], "attempt"
    )
    assert total["runs"] == 6


def image():
    return {
        "image_id": "root",
        "kind": "root",
        "sha256": D,
        "size_bytes": 1000,
        "stopped_ns": 1,
        "sealed_ns": 2,
        "format": "raw",
        "device": 1,
        "inode": 2,
        "persistence": [
            {"role": role, "root_relative_path": path, "filesystem_id": "rootfs", "external": False}
            for role, path in [
                ("os", "."),
                ("datastore", "var/lib/postgresql"),
                ("objects", "data/minio"),
                ("redis", "data/redis"),
            ]
        ],
    }


def test_one_actual_root_covers_all_persistence_roles():
    assert set(p.validate_images([m.Image.model_validate(image())])) == {"root"}


@pytest.mark.parametrize("mutation", ["missing", "external", "duplicate", "split", "escape"])
def test_unsupported_or_incomplete_physical_mapping_fails(mutation):
    raw = image()
    if mutation == "missing":
        raw["persistence"].pop()
    if mutation == "external":
        raw["persistence"][1]["external"] = True
    if mutation == "duplicate":
        raw["persistence"].append(raw["persistence"][0])
    if mutation == "escape":
        raw["persistence"][1]["root_relative_path"] = "../external"
    rows = [raw, deepcopy(raw)] if mutation == "split" else [raw]
    with pytest.raises(ValueError, match=r"mapping|coverage|image|role"):
        p.validate_images([m.Image.model_validate(x) for x in rows])


def sample_plan(mode="baseline", window=None, physical="baseline-physical"):
    return {
        "sample_id": "sample",
        "dimension": "admission",
        "mode": mode,
        "operation": "admission",
        "ordinal": 0,
        "target": {
            "scope_id": "scope",
            "run_id": "new",
            "public_id": "session",
            "revision": "1",
            "step_id": None,
        },
        "window_id": window,
        "physical_window_id": physical,
        "reset_id": None,
        "prewarm_completed_ns": None,
        "action_id": "action",
        "page_id": "page",
        "context_id": "context",
    }


def test_baseline_preregisters_a_physical_round_without_claiming_combined_load():
    raw = sample_plan()
    plan = m.Plan.model_validate(raw)
    assert plan.window_id is None
    assert plan.physical_window_id == "baseline-physical"
    for physical in (None, ""):
        with pytest.raises(ValueError, match=r"physical_window_id"):
            m.Plan.model_validate({**raw, "physical_window_id": physical})
    with pytest.raises(ValueError, match=r"physical"):
        m.Plan.model_validate(sample_plan("loaded", "loaded-window", "different"))


def test_baseline_round_cannot_bind_to_an_unregistered_or_duplicate_physical_window():
    _, _, raw = fixture()
    raw["origin"]["round"]["window_id"] = "baseline-physical"
    raw["cohorts"][0]["kind"] = "admission"
    _, _, row = parse(*fixture()[:2], raw)
    plans = {"sample": m.Plan.model_validate(sample_plan())}
    p.join_round_windows(plans, [row], {}, "attempt")
    bad = row.model_copy(deep=True)
    bad.origin.round.window_id = "wrong"
    with pytest.raises(ValueError, match=r"physical"):
        p.join_round_windows(plans, [bad], {}, "attempt")
    with pytest.raises(ValueError, match=r"baseline"):
        p.join_round_windows(plans, [], {}, "attempt")
    duplicate = m.Plan.model_validate({**sample_plan(), "sample_id": "other"})
    with pytest.raises(ValueError, match=r"baseline"):
        p.join_round_windows({**plans, "other": duplicate}, [row], {}, "attempt")


def test_baseline_ledger_consumes_preregistered_physical_identity_once(tmp_path):
    from scripts.execution_capacity.attempt import AttemptLedger

    plan = {"attempt_id": "attempt", "samples": [sample_plan()]}
    with AttemptLedger.create(tmp_path / "rounds", plan) as ledger:
        ledger.reserve("sample", "baseline-physical", seal_digest=D)
        with pytest.raises(ValueError, match=r"consumed"):
            ledger.reserve("sample", "baseline-physical", seal_digest=D)
    with (
        AttemptLedger.open(tmp_path / "rounds", plan) as ledger,
        pytest.raises(ValueError, match=r"consumed"),
    ):
        ledger.reserve("sample", "baseline-physical", seal_digest=D)


def test_report_summary_binds_exact_run_set_without_duplicating_large_inventory():
    from scripts.acceptance.capacity_physical import summarize_cohort

    row = m.Cohort.model_validate(cohort("evaluation_subject", ["b", "a"]))
    summary = summarize_cohort(row)
    assert "run_ids" not in summary
    assert summary["run_ids_digest"] == canonical_digest(["a", "b"])
    assert summary["source"]["runs"] == 2


@pytest.mark.parametrize(
    ("mode", "swapped"),
    [
        ("baseline", None),
        ("baseline", "admission"),
        ("loaded", None),
        ("loaded", "admission"),
        ("loaded", "live"),
    ],
)
def test_each_round_owns_its_sample_admission_and_own_window_claims(mode, swapped):
    from types import SimpleNamespace

    plans, rounds, windows = {}, [], {}
    base, _, template = fixture()
    for index, sid in enumerate(("a", "b")):
        physical = "physical-" + sid
        plan = sample_plan(mode, physical if mode == "loaded" else None, physical)
        plan.update(sample_id=sid, target={**plan["target"], "run_id": "admitted-" + sid})
        plans[sid] = m.Plan.model_validate(plan)
        row = deepcopy(template)
        row["origin"]["round"].update(round_id="round-" + sid, sample_id=sid, window_id=physical)
        row["origin"].update(boot_id="boot-" + sid, clone_id="round-" + sid)
        actual_sid = ("b", "a")[index] if swapped else sid
        additions = [
            cohort(
                "admission",
                ["admitted-" + (actual_sid if swapped == "admission" else sid)],
                origin=row["origin"],
            )
        ]
        if mode == "loaded":
            additions.append(
                cohort(
                    "live",
                    ["live-" + (actual_sid if swapped == "live" else sid)],
                    origin=row["origin"],
                )
            )
            windows[physical] = SimpleNamespace(
                round_origin=m.RoundOrigin.model_validate(row["origin"]["round"]),
                boot_id=row["origin"]["boot_id"],
                claims=[
                    m.Claim.model_validate(
                        {
                            "run_id": "live-" + sid,
                            "activity_id": "activity-" + sid,
                            "generation": 0,
                            "claim_generation": 1,
                            "call_identity": "call-" + sid,
                            "session_id": "session-" + sid,
                            "policy_id": "policy",
                            "configured_model": "acceptance-live",
                            "stream": True,
                            "boot_id": row["origin"]["boot_id"],
                        }
                    )
                ],
            )
        for addition in additions:
            addition["cohort_id"] += "-" + sid
        row["cohorts"] = additions
        row["owned_run_ids"] = [r for c in base + additions for r in c["run_ids"]]
        rounds.append(parse(base, additions[0], row)[2])
    typed_base = [m.Cohort.model_validate(c) for c in base]
    p.join_inventories(
        "seal",
        typed_base,
        rounds,
        typed_base + [c for row in rounds for c in row.cohorts],
        "attempt",
    )
    if swapped:
        with pytest.raises(ValueError, match=r"round.*(admission|live)"):
            p.join_round_windows(plans, rounds, windows, "attempt")
    else:
        p.join_round_windows(plans, rounds, windows, "attempt")
