"""Actual final observer connection. Fixed standard populations are isolated only.

All repositories, services, Run predicates, queries, storage and transport
parsers execute. Driver/OS boundaries use private SQLite and pipe fixtures.
"""

import pytest


@pytest.mark.asyncio
async def test_actual_final_to_original_replay_connection(tmp_path, monkeypatch):
    from scripts.execution_capacity.retained_final import replay_final
    from scripts.execution_capacity.test_final_connection_fixture import acquire_final

    value = await acquire_final(tmp_path, monkeypatch)
    source = await replay_final(
        value.roots,
        origin=value.origin,
        base=None,
        budget=value.budget,
        signing_secret=value.secret,
        cursor_secret=value.cursor,
    )
    assert source == value.final["source"]


@pytest.mark.parametrize("format_version", [1, 2])
def test_actual_base_context_reopens_and_copies_originals(tmp_path, monkeypatch, format_version):
    import asyncio

    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    original_root = None
    index_bytes = None
    if format_version == 2:
        (tmp_path / "guest").mkdir(mode=0o700)
        original_root = tmp_path / "guest" / "c2c-originals"
        index_bytes = 4 * 1024 * 1024
    value = asyncio.run(
        acquire_final(tmp_path, monkeypatch, original_root=original_root, index_bytes=index_bytes)
    )
    base = seal_acquired(tmp_path, monkeypatch, value)
    context = OfflineProofContext(
        bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
        rounds=[],
        signing_secret=value.secret,
        cursor_secret=value.cursor,
        budget=value.budget,
        index_bytes=index_bytes,
    )
    wrong_key = OfflineProofContext(
        bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
        rounds=[],
        signing_secret="fixture-wrong-original-key",
        cursor_secret=value.cursor,
        budget=value.budget,
        index_bytes=index_bytes,
    )
    with pytest.raises(PrivateProofError, match="original replay"):
        wrong_key.project()
    projected = context.project()
    assert len(projected.units) == 1
    with monkeypatch.context() as bounded:
        bounded.setattr("scripts.execution_capacity.offline_context.PROJECT_WORKING_BYTES", 1)
        with pytest.raises(PrivateProofError, match="original replay"):
            context.project()
    with monkeypatch.context() as bounded:
        bounded.setattr("scripts.execution_capacity.offline_context.PROJECT_ORIGINAL_BYTES", 1)
        with pytest.raises(PrivateProofError, match="original replay"):
            context.project()
    copied = context.copy(tmp_path / "copied-proof")
    assert copied.project() == projected
    if format_version == 2:
        from scripts.execution_capacity import offline_context

        actual_copy = offline_context.copy_private

        def changed_after_first(source, target, files, *, budget):
            def delayed():
                from contextlib import closing

                with closing(files) as rows:
                    yield next(rows)
                    name, receipt = next(rows)
                    (source / name).write_bytes(b"changed-after-complete-preflight")
                    yield name, receipt
                    yield from rows

            return actual_copy(source, target, delayed(), budget=budget)

        with monkeypatch.context() as changed:
            changed.setattr(offline_context, "copy_private", changed_after_first)
            with pytest.raises(PrivateProofError, match="copy"):
                context.copy(tmp_path / "failed-copy")
        assert (tmp_path / "failed-copy/unit-000000/plan.json").is_file()
        assert not (tmp_path / "failed-copy/unit-000001").exists()
    base.image_path.write_bytes(b"changed-image")
    with pytest.raises(PrivateProofError):
        copied.project()


@pytest.mark.parametrize("format_version", [1, 2])
def test_actual_round_context_replays_complete_base_and_fresh_copied_units(
    tmp_path, monkeypatch, format_version
):
    import asyncio

    from scripts.execution_capacity.offline_context import BaseLocation, OfflineProofContext
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        acquire_round,
        seal_acquired,
    )

    original_root = None
    index_bytes = None
    if format_version == 2:
        (tmp_path / "guest").mkdir(mode=0o700)
        original_root = tmp_path / "guest" / "c2c-originals"
        index_bytes = 4 * 1024 * 1024
    # Each fixture patch context restores the original driver before the next.
    with monkeypatch.context() as first:
        value = asyncio.run(
            acquire_final(tmp_path, first, original_root=original_root, index_bytes=index_bytes)
        )
    base = seal_acquired(tmp_path, monkeypatch, value)
    with monkeypatch.context() as second:
        from scripts.execution_capacity.original_journal import OriginalJournal

        opened_base = []
        real_open = OriginalJournal.open.__func__

        def reopen(cls, root, **kwargs):
            opened = real_open(cls, root, **kwargs)
            if root == base.retained_root / "c2c-originals":
                opened_base.append(opened)
            return opened

        second.setattr(OriginalJournal, "open", classmethod(reopen))
        _round_value, location = asyncio.run(acquire_round(tmp_path, second, base, value))
        if format_version == 2:
            assert opened_base
            assert all(opened.closed for opened in opened_base)
        context = OfflineProofContext(
            bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
            rounds=[location],
            signing_secret=value.secret,
            cursor_secret=value.cursor,
            budget=value.budget,
            index_bytes=index_bytes,
        )
        projected = context.project()
        assert [row.kind for row in projected.units] == ["base", "round"]
        assert projected.units[1].families["run-input"].count == 2
        copied = context.copy(tmp_path / "round-copy")
        assert copied.project() == projected


@pytest.mark.parametrize("mode", ["projection", "comparison", "copy"])
def test_actual_round_rechecks_parent_after_open_before_publication(tmp_path, monkeypatch, mode):
    import asyncio

    from scripts.execution_capacity import offline_context
    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        acquire_round,
        seal_acquired,
    )

    with monkeypatch.context() as first:
        value = asyncio.run(acquire_final(tmp_path, first))
    base = seal_acquired(tmp_path, monkeypatch, value)
    with monkeypatch.context() as second:
        _round_value, location = asyncio.run(acquire_round(tmp_path, second, base, value))
        context = OfflineProofContext(
            bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
            rounds=[location],
            signing_secret=value.secret,
            cursor_secret=value.cursor,
            budget=value.budget,
        )
        actual_project_unit = offline_context.project_unit
        changed = False

        def change_after_open(*args, **kwargs):
            nonlocal changed
            unit = actual_project_unit(*args, **kwargs)
            if kwargs["kind"] == "round" and not changed:
                changed = True
                ledger = location.parent_retained / "attempt.jsonl"
                ledger.write_bytes(ledger.read_bytes() + b"changed-after-open\n")
            return unit

        class Comparison:
            def base(self, *_):
                pass

            def unit(self, *_):
                pass

            def round(self, *_):
                pass

            def query(self, *_):
                pass

            def finish(self, *_):
                pass

        second.setattr(offline_context, "project_unit", change_after_open)
        action = {
            "projection": context.project,
            "comparison": lambda: context._checked(comparator=Comparison()),
            "copy": lambda: context.copy(tmp_path / "changed-parent-copy"),
        }[mode]
        with pytest.raises(PrivateProofError):
            action()
        assert changed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "extra-lease", "missing-read", "foreign-batch", "false-clean", "unknown-old"]
)
async def test_complete_historical_predicate_coverage(tmp_path, monkeypatch, fault):
    from copy import deepcopy

    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.predicate_history import validate_predicate_history
    from scripts.execution_capacity.test_final_connection_fixture import acquire_final

    value = await acquire_final(tmp_path, monkeypatch)
    final = deepcopy(value.roots["cleanup"]["quiescence"]["final"])
    records = final["predicate_journals"]
    if fault == "extra-lease":
        records["lease"]["foreign"] = deepcopy(next(iter(records["lease"].values())))
    elif fault == "missing-read":
        records["environment_read"].clear()
    elif fault == "false-clean":
        next(iter(records["lease"].values()))["receipt"]["namespace"] = "foreign"
    elif fault in {"foreign-batch", "unknown-old"}:
        key, read = next(iter(records["environment_read"].items()))
        del records["environment_read"][key]
        if fault == "foreign-batch":
            read["body"]["batch_id"] = "foreign"
        else:
            read["body"]["operations"][next(iter(read["body"]["operations"]))][0]["status"] = (
                "unknown"
            )
        records["environment_read"][canonical_digest(read["body"])] = read
    if fault:
        with pytest.raises(ValueError, match=r".+"):
            validate_predicate_history(final, budget=value.budget, sql=value.roots["sql"])
    else:
        validate_predicate_history(final, budget=value.budget, sql=value.roots["sql"])


@pytest.mark.parametrize(
    "fault",
    [
        "hmac",
        "boundary",
        "case-body",
        "writer-bytes",
        "writer-marker",
        "writer-time",
        "sql-params",
        "parent-absent",
    ],
)
def test_recomputed_manifest_cannot_authorize_changed_originals(tmp_path, monkeypatch, fault):
    import asyncio
    import base64

    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    value = asyncio.run(acquire_final(tmp_path, monkeypatch))
    originals = value.roots["operands"]
    if fault == "hmac":
        originals["signed-configuration"][0][0]["body"]["physical_requester"]["proof"]["principal"][
            "user_id"
        ] = "foreign"
    elif fault == "boundary":
        originals["playback-boundary-observation"][0]["row"]["formal_position"] = 999
    elif fault == "case-body":
        value.roots["objects"][0]["data"] = b"[]"
    elif fault == "sql-params":
        value.roots["sql"][0]["bound_parameters"]["id_1"] = "foreign"
    elif fault == "parent-absent":
        originals["journal-read"][0]["value"] = []
    else:
        row = value.roots["cleanup"]["quiescence"]["final"]["writers"]["exit_observations"][0][
            "transports"
        ][0]
        if fault == "writer-time":
            row["start_ns"] = 1
            row["end_ns"] = 2
        row["stdout"] = (
            (
                row["stdout"]
                if fault == "writer-time"
                else row["stdout"].replace(b'"Id": "writer"', b'"Id": "foreign"')
            )
            if fault in {"writer-bytes", "writer-time"}
            else {"$bytes": base64.b64encode(row["stdout"]).decode("ascii")}
        )
    base = seal_acquired(tmp_path, monkeypatch, value)
    context = OfflineProofContext(
        bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
        rounds=[],
        signing_secret=value.secret,
        cursor_secret=value.cursor,
        budget=value.budget,
    )
    with pytest.raises(PrivateProofError) as error:
        context.project()
    assert str(tmp_path) not in str(error.value)
    assert "fixture-original" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "absent", "changed", "wrong-index"])
async def test_historical_current_reads_require_actual_owner_sql(tmp_path, monkeypatch, fault):
    from copy import deepcopy

    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.predicate_history import validate_predicate_history
    from scripts.execution_capacity.test_final_connection_fixture import acquire_final

    value = await acquire_final(tmp_path, monkeypatch)
    final = deepcopy(value.roots["cleanup"]["quiescence"]["final"])
    sql = deepcopy(value.roots["sql"])
    records = final["predicate_journals"]["environment_read"]
    key, record = next(iter(records.items()))
    observation = record["body"]["query_observations"][0]
    if fault == "absent":
        sql = []
    elif fault == "changed":
        sql[observation["sql_index"]]["snapshot"] = "foreign-original-snapshot"
    elif fault == "wrong-index":
        observation["sql_index"] = 0
        del records[key]
        records[canonical_digest(record["body"])] = record
    if fault:
        with pytest.raises(ValueError, match="actual owner SQL"):
            validate_predicate_history(final, budget=value.budget, sql=sql)
    else:
        validate_predicate_history(final, budget=value.budget, sql=sql)


@pytest.mark.parametrize(
    "fault", ["missing-phase", "phase-time", "extra-diagnostic", "missing-diagnostic-input"]
)
def test_recomputed_quiescence_phase_claims_are_not_authority(tmp_path, monkeypatch, fault):
    import asyncio

    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    value = asyncio.run(acquire_final(tmp_path, monkeypatch))
    quiescence = value.roots["cleanup"]["quiescence"]
    if fault == "missing-phase":
        quiescence["phases"].pop(0)
    elif fault == "phase-time":
        quiescence["phases"][-1]["end_ns"] = quiescence["phases"][-1]["start_ns"]
    elif fault == "extra-diagnostic":
        quiescence["diagnostics"].append({"errors": [], "statements": []})
    else:
        phase = dict(quiescence["phases"][0])
        phase["stage"] = "diagnostics"
        quiescence["phases"].insert(1, phase)
        quiescence["diagnostics"].append(
            {"errors": [], "statements": [{"errors": []}], "ended_ns": phase["end_ns"]}
        )
    base = seal_acquired(tmp_path, monkeypatch, value)
    context = OfflineProofContext(
        bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
        rounds=[],
        signing_secret=value.secret,
        cursor_secret=value.cursor,
        budget=value.budget,
    )
    with pytest.raises(PrivateProofError, match="original replay"):
        context.project()


def test_deleted_history_requires_independently_reopened_complete_base(tmp_path, monkeypatch):
    import asyncio
    from copy import deepcopy

    from scripts.execution_capacity.predicate_history import validate_predicate_history
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    value = asyncio.run(acquire_final(tmp_path, monkeypatch))
    base = seal_acquired(tmp_path, monkeypatch, value)
    original = base.open_evidence(budget=value.budget).roots["cleanup"]["quiescence"]["final"]
    later = deepcopy(original)
    later["start_ns"] = original["end_ns"] + 1
    later["settlement"]["rows"]["evaluation_batches"] = []
    later["settlement"]["rows"]["evaluation_environment_leases"] = []
    # Unit coverage for inherited deletion only: actual final source/settlement
    # acquisition and the public facade remain separately mandatory.
    validate_predicate_history(later, budget=value.budget, sql=[], base=original)
    with pytest.raises(ValueError, match="orphan original historical"):
        validate_predicate_history(later, budget=value.budget, sql=[], base=None)
    foreign = deepcopy(original)
    foreign["predicate_journals"]["environment_read"].clear()
    with pytest.raises(ValueError, match="orphan original historical"):
        validate_predicate_history(later, budget=value.budget, sql=[], base=foreign)


def test_one_gib_cumulative_replay_quota_is_not_reset_between_calls(tmp_path, monkeypatch):
    import asyncio
    import json

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.original_shards import OriginalView
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        seal_acquired,
    )

    value = asyncio.run(acquire_final(tmp_path, monkeypatch))
    base = seal_acquired(tmp_path, monkeypatch, value)
    budget = EvidenceBudget(bytes_limit=1024**3, rows_limit=10_000_000, row_limit=4 * 1024**2)
    context = OfflineProofContext(
        bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
        rounds=[],
        signing_secret=value.secret,
        cursor_secret=value.cursor,
        budget=budget,
    )
    quotas = []
    materialized = []
    opened = []
    original_check = EvidenceBudget.check
    original_materialize = OriginalView.materialize
    original_open = OriginalView.open.__func__

    def check(self, *args, **kwargs):
        try:
            return original_check(self, *args, **kwargs)
        except EvidenceQuotaError:
            quotas.append((self.bytes, self.bytes_limit))
            raise

    def materialize(self):
        materialized.append(1)
        return original_materialize(self)

    def reopen(cls, *args, **kwargs):
        opened.append(1)
        return original_open(cls, *args, **kwargs)

    monkeypatch.setattr(EvidenceBudget, "check", check)
    monkeypatch.setattr(OriginalView, "materialize", materialize)
    monkeypatch.setattr(OriginalView, "open", classmethod(reopen))
    calls = 0
    for _ in range(8):
        calls += 1
        try:
            context.project()
        except PrivateProofError:
            break
    else:
        pytest.fail("bounded repeated replay did not exhaust shared original budget")
    assert quotas
    assert context._budget is budget
    manifests = list(base.retained_root.rglob("c2c-originals/manifest.json"))
    encoded = sum(
        path.stat().st_size for manifest in manifests for path in manifest.parent.glob("*.jsonl")
    )
    print(
        json.dumps(
            {
                "fixture": "base-connection-only",
                "encoded_original_shard_bytes": encoded,
                "one_gib_project_calls_including_rejected": calls,
                "view_opens": len(opened),
                "materializations": len(materialized),
                "logical_bytes_before_rejection": budget.bytes,
                "limit": budget.bytes_limit,
            },
            sort_keys=True,
        )
    )
