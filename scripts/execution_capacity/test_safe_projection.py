"""Strict complete safe families preserve raw commitments, never private fields."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from scripts.execution_capacity.cumulative_cleanup import TABLES
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.evidence_owner import FAMILIES


def graph():
    histories = {
        name: {}
        for name in (
            "object",
            "upload",
            "live_disposition",
            "live_admission",
            "live_window",
            "live_failure",
            "broker_request",
        )
    }
    histories["object"]["/private/fixture/key"] = {
        "body": {"raw": "private-payload", "value": 1},
        "receipt": {"raw": "private-receipt"},
    }
    final = {
        "source": {
            "owners": [],
            "attempts": [],
            "judges": [],
            "versions": [],
            "objects": [],
            "runs": [],
            "cohorts": [],
            "projectors": [],
        },
        "settlement": {"rows": {table: [] for table in (*TABLES, "execution_outbox")}},
        "retained_history": {"records": histories},
        "predicate_journals": {
            name: {}
            for name in ("lease", "lease_state", "environment_observation", "environment_read")
        },
        "writers": {},
        "storage": {},
        "broker": {},
        "physical_observations": [],
        "physical": {},
    }
    roots = {
        "cleanup": {"quiescence": {"final": final}},
        "operands": {name: [] for name in FAMILIES},
        "objects": [],
        "sql": [],
        "transports": [],
    }
    roots["operands"]["projection"] = [
        {"privatepath": "/private/fixture/original", "payload": "private-payload"}
    ]
    return roots


def test_complete_safe_projection_rejects_absence_and_commits_full_bodies():
    from scripts.execution_capacity.safe_projection import SafeUnit, project_unit

    raw = graph()
    view = SimpleNamespace(manifest={"shards": [], "records": 0, "nodes": 0})
    safe = project_unit(
        raw, view, kind="base", origin_sha256="a" * 64, base_sha256=None, budget=EvidenceBudget()
    )
    rendered = safe.model_dump_json()
    assert "/private/fixture" not in rendered
    assert "private-payload" not in rendered
    assert "private-receipt" not in rendered
    assert SafeUnit.model_validate(safe.model_dump()) == safe
    changed = deepcopy(raw)
    changed["cleanup"]["quiescence"]["final"]["retained_history"]["records"]["object"][
        "/private/fixture/key"
    ]["body"]["value"] = 2
    assert (
        project_unit(
            changed,
            view,
            kind="base",
            origin_sha256="a" * 64,
            base_sha256=None,
            budget=EvidenceBudget(),
        )
        != safe
    )
    for family in FAMILIES:
        missing = deepcopy(raw)
        del missing["operands"][family]
        with pytest.raises(ValueError, match=r".+"):
            project_unit(
                missing,
                view,
                kind="base",
                origin_sha256="a" * 64,
                base_sha256=None,
                budget=EvidenceBudget(),
            )
    document = safe.model_dump()
    document["families"].pop("projection")
    with pytest.raises(ValueError, match=r".+"):
        SafeUnit.model_validate(document)
    document = safe.model_dump()
    document["raw_path"] = "/private/fixture"
    with pytest.raises(ValueError, match=r".+"):
        SafeUnit.model_validate(document)


def owned_graph(root, raw):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.original_shards import OriginalView

    owner = EvidenceOwner(original_root=root, index_bytes=256 * 1024)
    try:
        for name, rows in raw["operands"].items():
            for row in rows:
                owner.retain(name, row)
        for name in ("sql", "objects", "transports"):
            for row in raw[name]:
                owner.journal.append(name, row)
        owner.finish_originals(
            {
                "cleanup": raw["cleanup"],
                "operands": owner.originals,
                "sql": owner.sql_reads,
                "objects": owner.journal.sequence("objects"),
                "transports": owner.journal.sequence("transports"),
            },
            {},
        )
    finally:
        owner.journal.close()
    return OriginalView.open(root, budget=EvidenceBudget(), index_bytes=256 * 1024)


def test_v2_projection_is_compact_complete_and_commits_original_values(tmp_path):
    from scripts.execution_capacity.safe_projection import project_unit, typed_digest

    raw = graph()
    raw["operands"]["projection"] *= 100
    with owned_graph(tmp_path / "originals", raw) as view:
        roots = view.materialize()
        safe = project_unit(
            roots,
            view,
            kind="base",
            origin_sha256="a" * 64,
            base_sha256=None,
            budget=EvidenceBudget(),
        )
        assert safe.schema_version == 2
        assert safe.families["projection"].count == 100
        assert safe.families["projection"].sha256 == typed_digest(
            raw["operands"]["projection"], budget=EvidenceBudget()
        )
        assert safe.families["projection"].model_dump().keys() == {"count", "sha256"}
        assert set(safe.families) == FAMILIES
        rendered = safe.model_dump_json()
        assert len(rendered) < 15_000
        assert "/private/fixture" not in rendered
        assert "private-payload" not in rendered
        assert "private-receipt" not in rendered


def test_owned_stream_commitment_matches_small_typed_oracle_and_rejects_foreign_view(tmp_path):
    from decimal import Decimal
    from uuid import UUID

    from scripts.execution_capacity.safe_projection import typed_digest

    raw = graph()
    raw["sql"] = [{"values": [UUID(int=7), Decimal("1.00"), b"bytes", None, True, 1, "1"]}]
    with (
        owned_graph(tmp_path / "first", raw) as first,
        owned_graph(tmp_path / "second", raw) as second,
    ):
        rows = first.materialize()["sql"]
        assert typed_digest(rows, budget=EvidenceBudget(), view=first) == typed_digest(
            raw["sql"], budget=EvidenceBudget()
        )
        with pytest.raises(ValueError, match="owner"):
            typed_digest(rows, budget=EvidenceBudget(), view=second)
        with pytest.raises(ValueError, match="owner"):
            typed_digest(rows, budget=EvidenceBudget())
        first.close()
        with pytest.raises(ValueError, match="closed"):
            typed_digest(rows, budget=EvidenceBudget(), view=first)


@pytest.mark.parametrize("mutation", ["order", "type", "body", "receipt", "empty"])
def test_v2_full_values_commitment_detects_original_mutations(tmp_path, mutation):
    from uuid import UUID

    from scripts.execution_capacity.safe_projection import project_unit

    raw = graph()
    raw["sql"] = [{"id": UUID(int=1)}, {"id": UUID(int=2)}]
    changed = deepcopy(raw)
    record = changed["cleanup"]["quiescence"]["final"]["retained_history"]["records"]["object"][
        "/private/fixture/key"
    ]
    if mutation == "order":
        changed["sql"].reverse()
    elif mutation == "type":
        changed["sql"][0]["id"] = str(UUID(int=1))
    elif mutation == "body":
        record["body"]["value"] = True
    elif mutation == "receipt":
        record["receipt"] = None
    else:
        record["receipt"] = {}
    projections = []
    for ordinal, value in enumerate((raw, changed)):
        with owned_graph(tmp_path / str(ordinal), value) as view:
            projections.append(
                project_unit(
                    view.materialize(),
                    view,
                    kind="base",
                    origin_sha256="a" * 64,
                    base_sha256=None,
                    budget=EvidenceBudget(),
                )
            )
    field = "sql" if mutation in ("order", "type") else "history"
    assert getattr(projections[0], field) != getattr(projections[1], field)


@pytest.mark.parametrize("mutation", ["missing-receipt", "missing-family", "extra-family"])
def test_v2_projection_rejects_incomplete_original_families(tmp_path, mutation):
    from scripts.execution_capacity.safe_projection import project_unit

    raw = graph()
    final = raw["cleanup"]["quiescence"]["final"]
    if mutation == "missing-receipt":
        del final["retained_history"]["records"]["object"]["/private/fixture/key"]["receipt"]
    elif mutation == "missing-family":
        del final["predicate_journals"]["lease"]
    else:
        final["predicate_journals"]["foreign"] = {}
    with (
        owned_graph(tmp_path / "originals", raw) as view,
        pytest.raises(ValueError, match=r"coverage|projection"),
    ):
        project_unit(
            view.materialize(),
            view,
            kind="base",
            origin_sha256="a" * 64,
            base_sha256=None,
            budget=EvidenceBudget(),
        )


def test_safe_v2_wire_is_strict_and_rejects_mixed_versions_and_wrong_occurrences(tmp_path):
    from scripts.acceptance.capacity_c2c_models import C2c, SafeUnitV2
    from scripts.execution_capacity.safe_projection import project_unit

    with owned_graph(tmp_path / "originals", graph()) as view:
        safe = project_unit(
            view.materialize(),
            view,
            kind="base",
            origin_sha256="a" * 64,
            base_sha256=None,
            budget=EvidenceBudget(),
        )
    assert SafeUnitV2.model_validate(safe.model_dump()) == safe
    document = safe.model_dump()
    document["families"]["projection"]["count"] += 1
    with pytest.raises(ValueError, match="occurrence"):
        SafeUnitV2.model_validate(document)
    for field in ("sql", "history", "source", "cleanup_sha256"):
        document = safe.model_dump()
        del document[field]
        with pytest.raises(ValueError, match="required"):
            SafeUnitV2.model_validate(document)
    document = safe.model_dump()
    document["raw_path"] = "/private/foreign"
    with pytest.raises(ValueError, match="Extra"):
        SafeUnitV2.model_validate(document)
    role = {
        "attempt_id": "fixture",
        "protocol_id": "fixture",
        "projection_version": 2,
        "units": [safe.model_dump()],
    }
    assert C2c.model_validate(role).units == [safe]
    role["projection_version"] = 1
    with pytest.raises(ValueError, match="versions"):
        C2c.model_validate(role)


def test_copied_v2_projection_is_rederived_from_fresh_originals(tmp_path):
    from scripts.execution_capacity.c2c_export import _receipt
    from scripts.execution_capacity.original_shards import OriginalView
    from scripts.execution_capacity.proof_copy import copy_private
    from scripts.execution_capacity.safe_projection import project_unit

    root = tmp_path / "originals"
    with owned_graph(root, graph()) as source:
        expected = project_unit(
            source.materialize(),
            source,
            kind="base",
            origin_sha256="a" * 64,
            base_sha256=None,
            budget=EvidenceBudget(),
        )
        files = {
            name: _receipt(root / name, EvidenceBudget())
            for name in ("manifest.json", "000000.bin", "occurrences.jsonl")
        }
        destination = tmp_path / "copy"
        copy_private(root, destination, files, budget=EvidenceBudget())
    with OriginalView.open(destination, budget=EvidenceBudget(), index_bytes=256 * 1024) as copied:
        assert (
            project_unit(
                copied.materialize(),
                copied,
                kind="base",
                origin_sha256="a" * 64,
                base_sha256=None,
                budget=EvidenceBudget(),
            )
            == expected
        )
    with (destination / "000000.bin").open("r+b") as stream:
        stream.write(b"!")
    with pytest.raises(ValueError, match="bytes"):
        OriginalView.open(destination, budget=EvidenceBudget(), index_bytes=256 * 1024)
