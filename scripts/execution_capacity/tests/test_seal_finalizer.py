"""Full callable orchestration with actual private bytes and OS/guest boundaries."""

import base64
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scripts.acceptance.capacity_io import canonical_digest as digest
from scripts.execution_capacity.attempt import AttemptLedger, encode
from scripts.execution_capacity.inventory import SourceInventory
from scripts.execution_capacity.seal_offline import hash_offline


def fixtures(uuid):
    origin = {"kind": "base", "seal_id": "seal", "round": None, "boot_id": None, "clone_id": None}
    cohorts = [
        {
            "origin": origin,
            "cohort_id": "cohort",
            "kind": "standard",
            "scope_id": "scope",
            "run_ids": ["run"],
            "source": {"runs": 1, "formal_events": 1, "observations": 1, "visible_steps": 1},
            "view": {"runs": 1, "formal_events": 1, "observations": 1, "visible_steps": 1},
            "parity_digest": "a" * 64,
        }
    ]
    source = SourceInventory(
        database={"database_system_identifier": "12345"}, cohorts=cohorts, reads_complete=True
    )
    raw_source = copy.deepcopy(vars(source))
    writers = {"writer": {"body": {"boot_id": "boot"}, "receipt": {"resource_closed": True}}}
    supervisors = {"writer": {"body": {"reports": []}, "receipt": None}}
    exits = [{"container_id": "container", "exited": True}]
    quiescence = {
        "complete": True,
        "errors": [],
        "ended_ns": 10,
        "writers": exits,
        "final": {
            "complete": True,
            "issues": [],
            "source": raw_source,
            "writers": {"writers": writers, "uploads": {}, "supervisors": supervisors},
        },
    }
    payload = {
        "seal_id": "seal",
        "source_inventory_digest": digest(source.safe()),
        "writers": writers,
        "uploads": {},
        "supervisors": supervisors,
        "exits": exits,
        "errors": [],
        "complete_quiescence_digest": digest(quiescence),
    }
    root = {
        "major_minor": "8:1",
        "fstype": "ext4",
        "source": "/dev/vda1",
        "disk": "vda",
        "serial": uuid.replace("-", "")[:20],
    }
    coverage = {
        "root": root,
        "paths": [
            {"role": role, "path": path, "mount": root}
            for role, path in (
                ("os", "/"),
                ("datastore", "/pg"),
                ("objects", "/minio"),
                ("redis", "/redis"),
                ("wal", "/pg/pg_wal"),
                ("writers", "/private/writers"),
                ("docker", "/var/lib/docker"),
                ("source", "/capacity"),
            )
        ],
    }
    metadata = {
        "seal_id": "seal",
        "fixture_id": "fixture",
        "fixture_manifest_digest": "f" * 64,
        "source_digest": "a" * 64,
        "configuration_digest": "c" * 64,
        "policy_id": "policy",
        "migration": "0012",
        "projector_source_version": "1",
        "read_algorithm_version": "1",
        "generation": "live",
        "metric_version": "1",
        "completed_corpus_batch_id": "batch",
    }
    cleanup = {
        "quiescence": quiescence,
        "errors": [],
        "observer_closed_ns": 11,
        "source_inventory": raw_source,
        "source_safe": source.safe(),
        "source_inventory_digest": digest(source.safe()),
        "complete_quiescence_digest": digest(quiescence),
        "writer_base": payload,
        "writer_quiescence_digest": digest(payload),
        "storage": {"coverage": coverage},
        "seal_metadata": metadata,
    }
    offline = {
        "control": {"state": "shut down", "system_identifier": "12345"},
        "coverage": coverage,
        "used_build": {"actual": "bytes"},
        "used_build_digest": digest({"actual": "bytes"}),
    }
    return {"cleanup": cleanup, "stop": {"stores_stopped_ns": 12}, "offline": offline}, metadata


@pytest.mark.parametrize(
    ("fault", "format_version"),
    [
        (None, 1),
        ("late_writer", 1),
        ("missing_readback", 1),
        ("unclean_pg", 1),
        ("qemu_exit", 1),
        ("lost_qga", 1),
        ("protocol", 1),
        ("prefix", 1),
        ("manifest", 1),
        ("budget", 1),
        (None, 2),
    ],
)
def test_finalizer_order_and_failure_never_hashes_mutable_image(
    tmp_path, monkeypatch, fault, format_version
):
    from scripts.execution_capacity import seal_finalizer as seal

    uuid = str(uuid4())
    values, metadata = fixtures(uuid)
    events = []
    if fault == "late_writer":
        values["cleanup"]["quiescence"]["complete"] = False
    if fault == "unclean_pg":
        values["offline"]["control"]["state"] = "in production"
    monkeypatch.setattr(
        "scripts.execution_capacity.attempt.host_clock",
        lambda: {
            "boot_id": "host",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )
    from scripts.execution_capacity.c2c_export import close_originals, snapshot_cleanup
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.guest_seal_entry import artifact_relative

    guest_root = tmp_path / "guest"
    guest_root.mkdir(mode=0o700)
    identity = {"source_digest": "a" * 64}
    config = {
        "seal_id": "seal",
        "protocol_id": "protocol",
        "identity": identity,
        "evidence_root": str(guest_root),
    }
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    original_owner = EvidenceOwner(
        original_root=guest_root / "c2c-originals" if format_version == 2 else None,
        index_bytes=64 * 1024 if format_version == 2 else None,
    )
    with AttemptLedger.create(
        guest_root / "operations",
        {"identity": identity, "config_digest": digest(config), "protocol_id": "protocol"},
    ) as original:
        original.evidence_owner = original_owner
        original.append("phase-intent", {"phase": "cleanup"})
        final = close_originals(
            guest_root,
            config,
            original,
            {
                "cleanup": values["cleanup"],
                "operands": original_owner.originals,
                "objects": original_owner.journal.sequence("objects")
                if format_version == 2
                else [],
                "transports": original_owner.journal.sequence("transports")
                if format_version == 2
                else [],
                "sql": original_owner.sql_reads,
            },
            budget=original_owner.budget,
        )
        if original_owner.journal is not None:
            original_owner.journal.close()
        values["cleanup"]["c2c_final"] = final
        original.append("phase-complete", {"phase": "cleanup"})
        exported = snapshot_cleanup(guest_root, original, final, budget=EvidenceBudget())
    wire_values = dict(values)
    if format_version == 2:
        from scripts.execution_capacity.cleanup_envelope import cleanup_envelope

        wire_values["cleanup"] = cleanup_envelope(final, budget=original_owner.budget)
    rule = {
        "config_digest": digest(config),
        "seal_id": "seal",
        "export": {"attempt_id": "attempt", "protocol_id": "protocol", **metadata},
        "phase_timeout_seconds": 30,
    }
    if fault == "protocol":
        rule["export"]["protocol_id"] = "foreign-protocol"
    elif fault == "prefix":
        path = guest_root / "c2c-ledger" / "attempt.jsonl"
        path.write_bytes(path.read_bytes() + b"{}\n")
    elif fault == "manifest":
        path = guest_root / "c2c-originals" / "manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
    with AttemptLedger.create(tmp_path / "ledger", {"seal": rule}) as ledger:
        vm = SimpleNamespace(
            ledger=ledger,
            plan=SimpleNamespace(uuid=uuid),
            identity={"pid": 42},
            process=SimpleNamespace(pid=42),
            pidfd=100,
        )

        def exit_vm(seconds):
            events.append("qemu-exit")
            vm.pidfd = None
            code = 7 if fault == "qemu_exit" else 0
            ledger.append("qemu-exited", {"identity": vm.identity, "returncode": code})
            return code

        vm.wait_exit = exit_vm

        class Session:
            def __init__(self):
                self.ledger = ledger
                self.identity = {"source_digest": "a" * 64}
                self.agent = SimpleNamespace(shutdown=self.powerdown)

            def powerdown(self):
                events.append("guest-shutdown")
                if fault == "lost_qga":
                    raise EOFError("lost QGA")

            def seal_phase(self, phase, *, artifact=None, offset=None):
                if phase == "read":
                    if artifact.startswith("c2c-"):
                        data = (guest_root / artifact_relative(artifact)).read_bytes()
                        if artifact == "c2c-manifest":
                            events.append("c2c-fetch")
                    else:
                        data = encode(wire_values[artifact])
                    if fault == "missing_readback" and artifact == "cleanup":
                        data += b"x"
                    return {
                        "artifact": artifact,
                        "offset": offset,
                        "size_bytes": len(data),
                        "data": base64.b64encode(data[offset:]).decode(),
                        "eof": True,
                    }
                events.append(phase)
                data = encode(wire_values[phase])
                return {
                    "artifact": {"sha256": digest(wire_values[phase]), "size_bytes": len(data)},
                    "c2c_export": exported if phase == "cleanup" else None,
                }

        monkeypatch.setattr(seal, "verify_live_vm", lambda *args: events.append("owned-graph"))
        monkeypatch.setattr(seal, "process_identity", lambda pid: {"pid": pid})

        def convert(vm, output, **kwargs):
            assert events[-1] == "qemu-exit"
            assert vm.pidfd is None
            events.append("offline-hash")
            output.write_bytes(b"private-raw-bytes")
            output.chmod(0o600)
            return hash_offline(output)

        monkeypatch.setattr(seal, "flatten", convert)
        output = tmp_path / "sealed.raw"
        from scripts.execution_capacity.evidence_owner import EvidenceOwner

        final_budget = EvidenceOwner().budget
        if fault == "budget":
            final_budget.bytes_limit = final_budget.bytes + 3 * len(encode(values["cleanup"]))

        def finalize_owned():
            from scripts.execution_capacity import c2c_export

            real_read, real_write, real_open, real_verify = (
                seal.read_private,
                seal.write_private,
                seal.VerifiedBase.open,
                c2c_export.verify_export,
            )
            observed = []

            def read(path, *, budget=None, **kwargs):
                assert budget is final_budget
                observed.append("read")
                return real_read(path, budget=budget, **kwargs)

            def write(path, value, *, budget=None):
                assert budget is final_budget
                observed.append("write")
                return real_write(path, value, budget=budget)

            def reopen(cls, ledger, *, budget=None, image_path=None, index_bytes=None):
                assert budget is final_budget
                observed.append("reopen")
                return real_open(
                    ledger, budget=budget, image_path=image_path, index_bytes=index_bytes
                )

            def verify(*args, **kwargs):
                assert kwargs["budget"] is final_budget
                observed.append("verify")
                return real_verify(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(seal, "read_private", read)
                patch.setattr(seal, "write_private", write)
                patch.setattr(seal.VerifiedBase, "open", classmethod(reopen))
                patch.setattr(c2c_export, "verify_export", verify)
                if fault == "budget":
                    import json

                    real_loads = json.loads

                    def checked_loads(raw, *args, **kwargs):
                        assert raw != encode(values["cleanup"]), (
                            "exhausted host unit reached cleanup parser"
                        )
                        return real_loads(raw, *args, **kwargs)

                    patch.setattr(json, "loads", checked_loads)
                result = seal.finalize(
                    vm,
                    Session(),
                    object(),
                    output,
                    budget=final_budget,
                    index_bytes=64 * 1024 if format_version == 2 else None,
                )
            assert observed.count("read") >= 4
            assert {"write", "reopen", "verify"} <= set(observed)
            return result

        if fault:
            with pytest.raises((ValueError, EOFError)):
                finalize_owned()
            assert "offline-hash" not in events
            assert not ledger.records("seal-complete")
            assert ledger.records("seal-failed")
        else:
            actual = finalize_owned()
            import asyncio

            exercise_retained_base_location(tmp_path, actual)
            exercise_reopen_budget(monkeypatch, actual)
            exercise_reopen_failure_closes_views(monkeypatch, actual)
            asyncio.run(exercise_owned_round(tmp_path, monkeypatch, actual))
            quota_root = tmp_path / "quota-round"
            quota_root.mkdir(mode=0o700)
            asyncio.run(exercise_owned_round(quota_root, monkeypatch, actual, quota=True))
            fsync_root = tmp_path / "fsync-round"
            fsync_root.mkdir(mode=0o700)
            asyncio.run(
                exercise_owned_round(fsync_root, monkeypatch, actual, parent_fsync_failure=True)
            )
            assert events == [
                "owned-graph",
                "cleanup",
                "c2c-fetch",
                "stop",
                "offline",
                "owned-graph",
                "guest-shutdown",
                "qemu-exit",
                "offline-hash",
            ]
            assert (
                actual.clone_input()["writer_base"]["quiescence_digest"]
                == values["cleanup"]["writer_quiescence_digest"]
            )
            assert actual.seal.images[0].sha256 == hash_offline(output)["sha256"]
            assert "raw_path" not in actual.seal.model_dump()
            output.write_bytes(b"changed")
            with pytest.raises(ValueError, match="changed"):
                actual.clone_input()


async def exercise_owned_round(
    tmp_path, monkeypatch, base, *, quota=False, parent_fsync_failure=False
):
    """Actual parent/child ledger chain and wrapper-owned quiesce; effects injected."""
    import time
    from contextlib import ExitStack

    from scripts.acceptance.capacity_models import Cohort, SourceOrigin
    from scripts.execution_capacity.evidence_objects import EvidenceObjects
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.evidence_transport import EvidenceTransport
    from scripts.execution_capacity.final_inventory import FinalInventory
    from scripts.execution_capacity.reference_round import reserve_round
    from scripts.execution_capacity.round_originals import OwnedRoundOriginals

    sample = {"sample_id": "sample", "physical_window_id": "window"}
    parent_plan = {
        "attempt_id": "parent",
        "protocol_id": base.seal.protocol_id,
        "samples": [sample],
        "writer_base": base.private["writer_base"],
    }
    round_id, clone_id, boot_id = (str(uuid4()) for _ in range(3))
    parent_root = tmp_path / "parent"
    with AttemptLedger.create(parent_root, parent_plan) as parent:
        (parent_root / "rounds").mkdir(mode=0o700)
        child_root = parent_root / "rounds" / round_id
        child_plan = {
            "protocol_id": base.seal.protocol_id,
            "samples": [sample],
            "writer_base": base.private["writer_base"],
            "round": {
                "parent_attempt_id": "parent",
                "round_id": round_id,
                "sample_id": "sample",
                "window_id": "window",
            },
            "vm": {
                "uuid": clone_id,
                "base": base.private["raw_path"],
                "base_identity": base.private["raw_identity"],
                "overlay": str(child_root / "root.qcow2"),
            },
        }
        intent = {
            "attempt_id": round_id,
            "sample_id": "sample",
            "window_id": "window",
            "nonce": str(uuid4()),
            "source_digest": "fixture-source",
        }
        child_plan["guest_sessions"] = [intent]
        origin = reserve_round(
            parent, child_plan, child_root, seal_digest=base.private["raw_identity"]["sha256"]
        )
        with AttemptLedger.create(child_root, child_plan) as child, ExitStack() as history_lifetime:
            child.bind_clock()
            child.reserve("sample", "window", seal_digest=base.private["raw_identity"]["sha256"])
            child.append(
                "overlay-create-intent",
                {
                    "uuid": clone_id,
                    "base": child_plan["vm"]["base"],
                    "overlay": child_plan["vm"]["overlay"],
                },
            )
            child.append(
                "overlay-created",
                {
                    "uuid": clone_id,
                    "identity": {"device": 1, "inode": 2},
                    "chain": [
                        {
                            "format": "qcow2",
                            "full-backing-filename": child_plan["vm"]["base"],
                            "virtual-size": 100,
                        },
                        {
                            "format": "raw",
                            "filename": child_plan["vm"]["base"],
                            "virtual-size": 100,
                        },
                    ],
                },
            )
            child.append("guest-discovery-intent", {"identity": intent})
            child.append("guest-discovered", {"identity": {**intent, "boot_id": boot_id}})
            owner = EvidenceOwner()
            resources = SimpleNamespace(
                evidence=owner,
                evidence_objects=EvidenceObjects(object(), owner.budget),
                evidence_transport=EvidenceTransport(owner.budget),
                closed_ns=None,
            )
            if base.index_bytes is None:
                payload, source = base.load_history()
            else:
                history = history_lifetime.enter_context(base.open_evidence(budget=owner.budget))
                cleanup = history.roots["cleanup"]
                payload = cleanup["writer_base"]
                source = dict(cleanup["source_inventory"])
                source["cohorts"] = [Cohort.model_validate(row) for row in source["cohorts"]]
                source = SourceInventory(**source).require_complete()

            class Writers:
                base = SimpleNamespace(
                    writers=payload["writers"],
                    uploads=payload["uploads"],
                    supervisors=payload["supervisors"],
                )

                async def stop_admissions(self):
                    pass

                async def stop(self):
                    return [{**row, "observed_ns": time.monotonic_ns()} for row in payload["exits"]]

            reader = SimpleNamespace(
                evidence=owner,
                storage=resources.evidence_objects,
                origin=SourceOrigin(
                    kind="round",
                    seal_id=base.seal.seal_id,
                    round=origin.safe(),
                    clone_id=clone_id,
                    boot_id=boot_id,
                ),
            )
            final = FinalInventory(
                writers=Writers(),
                reader=reader,
                journal=None,
                storage=object(),
                binding={},
                docker=resources.evidence_transport,
                base=(source,),
            )

            async def read():
                before = time.monotonic_ns()
                owner.retain("query-rows", {"original-current-round": "fixture"})
                return {
                    "complete": True,
                    "issues": [],
                    "source": source,
                    "writers": payload,
                    "start_ns": before,
                    "end_ns": time.monotonic_ns(),
                }

            monkeypatch.setattr(final, "read", read)
            actual_origin = reader.origin
            reader.origin = reader.origin.model_copy(update={"boot_id": str(uuid4())})
            with pytest.raises(ValueError, match="guest/cohort origin"):
                OwnedRoundOriginals(parent, child, base, resources=resources, final=final)
            reader.origin = actual_origin
            # Rejected independent ownership attempt consumed its own unit.
            owner = EvidenceOwner()
            resources.evidence = reader.evidence = owner
            resources.evidence_objects = reader.storage = EvidenceObjects(object(), owner.budget)
            resources.evidence_transport = final.docker = EvidenceTransport(owner.budget)
            wrapper = OwnedRoundOriginals(parent, child, base, resources=resources, final=final)
            with pytest.raises(ValueError, match="actual collected"):
                wrapper.finish()
            result = await wrapper.collect(workloads=[], diagnostics=[], uploads=[])
            assert result.complete, result.errors
            with pytest.raises(ValueError, match="already consumed"):
                await wrapper.collect(workloads=[], diagnostics=[], uploads=[])
            with pytest.raises(ValueError, match="observer closure"):
                wrapper.finish()
            resources.closed_ns = time.monotonic_ns()
            if quota:
                import json

                from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

                owner.budget.bytes_limit = owner.budget.bytes
                with pytest.raises(EvidenceQuotaError):
                    wrapper.finish()
                assert not child.records("c2c-private-final")
                failed = json.loads((child.root / "c2c-failed-originals.json").read_bytes())
                assert failed["complete"] is False
                return
            from scripts.execution_capacity import original_shards

            synced = []
            real_sync = original_shards._sync_directory

            def sync(path):
                assert not child.records("c2c-private-final")
                synced.append(path)
                if parent_fsync_failure and path == child.root:
                    raise OSError("fixture parent fsync")
                return real_sync(path)

            with monkeypatch.context() as patch:
                patch.setattr(original_shards, "_sync_directory", sync)
                if parent_fsync_failure:
                    with pytest.raises(OSError, match="parent fsync"):
                        wrapper.finish()
                    assert not child.records("c2c-private-final")
                    assert (child.root / "c2c-originals").is_dir()
                    assert (child.root / "c2c-failed-originals.json").exists()
                    return
                view = wrapper.finish()
            assert synced == [child.root / "c2c-originals", child.root]
            assert (
                view.manifest["binding"]["base"]["manifest"] == base.open_evidence().view.manifest
            )
            assert not (child_root / "base-originals").exists()
            assert view.materialize()["operands"]["query-rows"] == [
                {"original-current-round": "fixture"}
            ]
            assert child.count("c2c-private-final") == 1
            with pytest.raises(ValueError, match="already consumed"):
                wrapper.finish()


def exercise_reopen_budget(monkeypatch, base):
    from scripts.execution_capacity import c2c_export, seal_finalizer
    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    budget = EvidenceOwner().budget
    reads, exports = [], []
    real_read, real_verify = seal_finalizer.read_private, c2c_export.verify_export

    def read(path, *, budget=None):
        reads.append(budget)
        return real_read(path, budget=budget)

    def verify(*args, **kwargs):
        exports.append(kwargs["budget"])
        return real_verify(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(seal_finalizer, "read_private", read)
        patch.setattr(c2c_export, "verify_export", verify)
        with base.open_evidence(budget=budget) as reopened:
            assert reopened.base.seal == base.seal
        if getattr(reopened.view, "journal", None) is not None:
            assert reopened.view.journal.closed
    assert reads
    assert len(exports) == 2
    assert all(value is budget for value in reads + exports)
    # Exhaust the shared allowance after the first actual parsed artifact.
    # The next file hash/parse cannot start under a freshly-created budget.
    used = budget.bytes

    def consume(path, *, budget=None):
        value = real_read(path, budget=budget)
        budget.reserve(budget.bytes_limit - budget.bytes, rows=0)
        return value

    with monkeypatch.context() as patch:
        patch.setattr(seal_finalizer, "read_private", consume)
        with pytest.raises(EvidenceQuotaError):
            base.open_evidence(budget=budget)
    assert budget.bytes > used
    assert len(exports) == 2


def exercise_reopen_failure_closes_views(monkeypatch, base):
    from scripts.execution_capacity import c2c_export, seal_finalizer
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    real_verify = c2c_export.verify_export
    opened = []

    def verify(*args, **kwargs):
        view, original = real_verify(*args, **kwargs)
        opened.append(view)
        return view, original

    def fail_cleanup(value, seal_id, *, owner=None, budget=None):
        raise ValueError("fixture cleanup predicate failure")

    with monkeypatch.context() as patch:
        patch.setattr(c2c_export, "verify_export", verify)
        patch.setattr(seal_finalizer, "validate_cleanup", fail_cleanup)
        with pytest.raises(ValueError, match="fixture cleanup predicate"):
            base.open_evidence(budget=EvidenceOwner().budget)
    assert len(opened) == 2
    for view in opened:
        if getattr(view, "journal", None) is not None:
            assert view.journal.closed


def exercise_retained_base_location(tmp_path, actual):
    """A copied base reopens copied originals while preserving the original origin."""
    import shutil

    from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.seal_finalizer import VerifiedBase

    destination = tmp_path / "retained-base"
    shutil.copytree(actual.ledger_root, destination)
    original_plan = (destination / "plan.json").read_bytes()
    budget = EvidenceOwner().budget
    ledger = ReadOnlyAttemptLedger.open(destination, origin=actual.ledger_root, budget=budget)
    copied = VerifiedBase.open(
        ledger, image_path=actual.private["raw_path"], budget=budget, index_bytes=actual.index_bytes
    )
    assert copied.ledger_root == actual.ledger_root
    assert copied.retained_root == destination
    evidence = copied.open_evidence(budget=budget)
    assert evidence.host_ledger.location == destination
    assert evidence.descriptor()["origin"] == str(actual.ledger_root)
    assert (destination / "plan.json").read_bytes() == original_plan
    (destination / "seal-guest-cleanup.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match=r".+"):
        copied.open_evidence(budget=EvidenceOwner().budget)
