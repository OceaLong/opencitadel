"""Actual independently reopened base is the only external original namespace."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from scripts.execution_capacity.original_journal import OriginalJournal


def test_actual_verified_base_collection_outlives_caller_and_reopens_explicitly(
    tmp_path, monkeypatch
):
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    (tmp_path / "guest").mkdir(mode=0o700)
    value = asyncio.run(
        acquire_final(
            tmp_path,
            monkeypatch,
            original_root=tmp_path / "guest" / "c2c-originals",
            index_bytes=4 * 1024 * 1024,
        )
    )
    base = seal_acquired(tmp_path, monkeypatch, value)
    source = base.open_evidence(budget=value.budget)
    root = tmp_path / "round-originals"
    with OriginalJournal.create(root, budget=value.budget, index_bytes=4 * 1024 * 1024) as owner:
        try:
            original = source.roots["cleanup"]["source_inventory"]["owners"]
            expected = list(original)
            owner.bind_verified_base(source)
            rows = owner.reference_base_collection(original)
        finally:
            source.close()
        assert list(rows) == expected
        assert rows.owner is owner
        for mutation in ("count", "producer", "namespace", "base"):
            descriptor = deepcopy(rows.body_descriptor)
            changed = rows
            if mutation == "count":
                changed = replace(rows, length=rows.length + 1)
            elif mutation == "producer":
                changed = replace(rows, producer_ordinal=rows.producer_ordinal + 1)
            elif mutation == "namespace":
                descriptor["namespace"] = "caller"
            else:
                descriptor["base"]["sealed_artifact"] = "0" * 64
            changed = replace(changed, body_descriptor=descriptor)
            with pytest.raises(ValueError, match="reference"):
                len(changed)
        owner.append("cleanup", {"rows": rows, "same": rows})
        owner.seal({"unit": "round"}, original_roots=True)
        assert not any(path.name.startswith("base-") for path in root.iterdir())
    with pytest.raises(ValueError, match="closed"):
        len(rows)
    with pytest.raises(ValueError, match=r"base|namespace"):
        OriginalJournal.open(root, budget=value.budget, index_bytes=4 * 1024 * 1024)
    with (
        base.open_evidence(budget=value.budget) as fresh_base,
        OriginalJournal.open(
            root, budget=value.budget, index_bytes=4 * 1024 * 1024, verified_base=fresh_base
        ) as fresh,
    ):
        cleanup = fresh.roots()["cleanup"]
        assert list(cleanup["rows"]) == expected
        assert cleanup["rows"] is cleanup["same"]


def test_actual_verified_base_dictionary_modes_and_nested_target_ownership(tmp_path, monkeypatch):
    from scripts.execution_capacity.inventory_sql import plain
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    (tmp_path / "guest").mkdir(mode=0o700)
    value = asyncio.run(
        acquire_final(
            tmp_path,
            monkeypatch,
            original_root=tmp_path / "guest" / "c2c-originals",
            index_bytes=4 * 1024 * 1024,
        )
    )
    owner = value.owner
    assert owner._cleanup_token is not None
    family = value.roots["cleanup"]["quiescence"]["final"]["predicate_journals"]["environment_read"]
    writer = owner.journal.begin_dictionary(owner._cleanup_token, "environment-read-history")
    keys = list(family)
    for key, row in family.items():
        writer.append(key, row)
    value.roots["cleanup"]["quiescence"]["final"]["predicate_journals"]["environment_read"] = (
        writer.complete()
    )
    base = seal_acquired(tmp_path, monkeypatch, value)
    with base.open_evidence(budget=value.budget) as source:
        original = source.roots["cleanup"]["quiescence"]["final"]["predicate_journals"][
            "environment_read"
        ]
        with OriginalJournal.create(
            tmp_path / "round-map", budget=value.budget, index_bytes=4 * 1024 * 1024
        ) as target:
            target.bind_verified_base(source)
            raw = target.reference_base_collection(original)
            converted = target.reference_base_collection(plain(original))
            source.close()
            assert list(raw) == keys
            assert list(converted) == sorted(keys)
            nested = raw[keys[0]]["body"]["leases"]
            assert nested.owner is target
            assert len(nested) > 0
            plain_nested = converted[keys[0]]["body"]["leases"]
            assert plain_nested.owner is target
            assert len(plain_nested) == len(nested)
            target.append("cleanup", {"raw": raw, "plain": converted, "alias": raw})
            target.seal({}, original_roots=True)
    with (
        base.open_evidence(budget=value.budget) as source,
        OriginalJournal.open(
            tmp_path / "round-map",
            budget=value.budget,
            index_bytes=4 * 1024 * 1024,
            verified_base=source,
        ) as fresh,
    ):
        root = fresh.roots()["cleanup"]
        assert root["raw"] is root["alias"]
        assert root["raw"] is not root["plain"]
        assert root["raw"][keys[0]]["body"]["leases"].owner is fresh
        assert list(root["plain"]) == sorted(keys)
