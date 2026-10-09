"""Actual ordered base finalizer. Every failure retains private artifacts/images.

The public Seal is derived only after actual cleanup, store/guest/QEMU exits and
independent offline readbacks. This does not run the C3 sample scheduler.
"""

import base64
import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_models import Image, PersistenceMapping, Seal
from scripts.acceptance.capacity_physical import validate_images
from scripts.execution_capacity.guest_seal import read_private, write_private
from scripts.execution_capacity.reference_vm import direct_fds, process_identity, verify_nodes
from scripts.execution_capacity.seal_offline import flatten, hash_offline
from scripts.execution_capacity.writer_base import _complete


def shutdown(vm, agent):
    if vm.ledger.records("guest-shutdown-intent") or vm.ledger.records("qemu-force-exit-intent"):
        raise ValueError("shutdown already consumed or forced recovery retained")
    if process_identity(vm.process.pid) != vm.identity:
        raise ValueError("actual owned QEMU process changed before shutdown")
    vm.ledger.append(
        "guest-shutdown-intent",
        {"uuid": vm.plan.uuid, "identity": vm.identity, "host_ns": time.monotonic_ns()},
    )
    if process_identity(vm.process.pid) != vm.identity:
        raise ValueError("actual owned QEMU changed after shutdown intent")
    agent.shutdown()
    if vm.wait_exit(60) != 0:
        raise ValueError("abnormal QEMU exit; never a clean seal")
    return time.monotonic_ns()


def fetch_private(session, phase, receipt, *, budget=None):
    """Fixed bounded pages into a private file; validate full original digest."""
    from scripts.acceptance.capacity_io import _parent
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.guest_seal_entry import artifact_relative

    relative = artifact_relative(phase)
    path = session.ledger.root / (
        relative if phase.startswith("c2c-") else "seal-guest-" + relative
    )
    budget = budget if budget is not None else EvidenceBudget(bytes_limit=32 * 1024 * 1024)
    size = receipt["size_bytes"]
    if type(size) is not int or not 0 < size <= 32 * 1024 * 1024:
        raise ValueError("private inventory artifact bound")
    budget.reserve(size * 3, rows=1)
    parent, name = _parent(
        session.ledger.root, path.relative_to(session.ledger.root).as_posix(), create=True
    )
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            os.close(fd)
            raise ValueError("private original destination required")
        os.fsync(parent)
    finally:
        os.close(parent)
    hashed, offset = hashlib.sha256(), 0
    with os.fdopen(fd, "wb") as stream:
        while offset < size:
            row = session.seal_phase("read", artifact=phase, offset=offset)
            if not isinstance(row.get("data"), str) or len(row["data"]) > 174764:
                raise ValueError("bounded original private artifact page required")
            data = base64.b64decode(row["data"], validate=True)
            if (
                row["artifact"] != phase
                or row["offset"] != offset
                or row["size_bytes"] != size
                or not data
                or len(data) > 128 * 1024
                or offset + len(data) > size
                or row["eof"] != (offset + len(data) == size)
            ):
                raise ValueError("actual private artifact page differs")
            stream.write(data)
            hashed.update(data)
            offset += len(data)
        stream.flush()
        os.fsync(stream.fileno())
    if hashed.hexdigest() != receipt["sha256"]:
        raise ValueError("complete private inventory artifact digest differs")
    return path


def verify_live_vm(vm, qmp):
    if (
        process_identity(vm.process.pid) != vm.identity
        or qmp.command("query-uuid")["UUID"] != vm.plan.uuid
    ):
        raise ValueError("actual seal VM identity differs")
    blocks = qmp.command("query-block")
    if len(blocks) != 1 or blocks[0]["inserted"]["node-name"] != "round-root":
        raise ValueError("extra or changed actual root block graph")
    rows = verify_nodes(
        qmp.command("query-named-block-nodes", {"flat": False}),
        vm.plan,
        direct_fds(vm.process.pid, [vm.plan.base, vm.plan.overlay]),
    )
    vm.ledger.append(
        "seal-live-graph", {"uuid": vm.plan.uuid, "nodes": rows, "host_ns": time.monotonic_ns()}
    )


def validate_cleanup(value, seal_id, *, owner=None, budget=None):
    from scripts.execution_capacity.evidence_json import chunks, equal_streams, json_digest

    def committed(item):
        return (
            canonical_digest(item)
            if owner is None
            else json_digest(item, owner=owner, budget=budget)
        )

    def equal(left, right):
        if owner is None:
            return left == right
        return equal_streams(
            chunks(left, owner=owner, budget=budget), chunks(right, owner=owner, budget=budget)
        )

    quiescence = value["quiescence"]
    source = value["source_inventory"]
    if (
        value["errors"]
        or not quiescence["complete"]
        or quiescence["errors"]
        or not source["reads_complete"]
        or source["errors"]
        or not value["observer_closed_ns"]
        or value["observer_closed_ns"] < quiescence["ended_ns"]
    ):
        raise ValueError("complete actual after-exit inventory/observer closure missing")
    if value["complete_quiescence_digest"] != committed(quiescence) or value[
        "source_inventory_digest"
    ] != committed(value["source_safe"]):
        raise ValueError("private complete source/quiescence digest differs")
    from scripts.execution_capacity.inventory import SourceInventory

    if (
        not equal(SourceInventory(**source).safe(owner=owner, budget=budget), value["source_safe"])
        or not equal(quiescence["final"]["source"], source)
        or quiescence["final"]["complete"] is not True
        or quiescence["final"]["issues"]
    ):
        raise ValueError("actual complete source readback differs")
    payload = value["writer_base"]
    _complete(payload)
    if not equal(payload["exits"], quiescence["writers"]) or any(
        not equal(payload[key], quiescence["final"]["writers"][key])
        for key in ("writers", "uploads", "supervisors")
    ):
        raise ValueError("original quiescence writer rows differ")
    if (
        payload["seal_id"] != seal_id
        or payload["source_inventory_digest"] != value["source_inventory_digest"]
        or committed(payload) != value["writer_quiescence_digest"]
        or payload["complete_quiescence_digest"] != value["complete_quiescence_digest"]
    ):
        raise ValueError("complete writer history linkage differs")
    if not source["cohorts"] or any(
        c["origin"]["kind"] != "base" or c["origin"]["seal_id"] != seal_id
        for c in source["cohorts"]
    ):
        raise ValueError("round increments cannot be relabeled immutable base")


def resolved_cleanup(wire, view, final, seal_id, budget):
    from scripts.execution_capacity.cleanup_envelope import resolve_cleanup_envelope
    from scripts.execution_capacity.evidence_json import json_digest

    if hasattr(view, "journal"):
        value = resolve_cleanup_envelope(wire, view=view, final=final, budget=budget)
        validate_cleanup(value, seal_id, owner=view.journal, budget=budget)
        return value
    if "schema" in wire or wire.get("c2c_final") != final:
        raise ValueError("strict legacy cleanup wire required")
    validate_cleanup(wire, seal_id)
    if json_digest(view.materialize()["cleanup"], budget=budget) != json_digest(
        {key: item for key, item in wire.items() if key != "c2c_final"}, budget=budget
    ):
        raise ValueError("typed original cleanup differs from fetched cleanup")
    return wire


def finalize(vm, session, qmp, output, *, budget=None, index_bytes=None):
    """Single consumed callable; prerequisites/config are immutable ledger intent."""
    ledger, rule = vm.ledger, vm.ledger.plan["seal"]
    if (
        session.ledger is not ledger
        or ledger.records("seal-finalize-intent")
        or rule["seal_id"] != rule["export"]["seal_id"]
    ):
        raise ValueError("exact fresh seal attempt required")
    if session.identity["source_digest"] != rule["export"]["source_digest"]:
        raise ValueError("actual guest/source differs from immutable seal intent")
    ledger.append(
        "seal-finalize-intent",
        {
            "identity": session.identity,
            "uuid": vm.plan.uuid,
            "output": str(output),
            "host_ns": time.monotonic_ns(),
        },
    )
    stage = "ownership"
    cleanup_view = None
    try:
        verify_live_vm(vm, qmp)
        from scripts.execution_capacity.c2c_export import fetch_export
        from scripts.execution_capacity.evidence_owner import EvidenceOwner

        budget = budget if budget is not None else EvidenceOwner().budget
        values, receipts = {}, {}
        c2c_receipt = None
        for stage in ("cleanup", "stop", "offline"):
            response = session.seal_phase(stage)
            receipts[stage] = response["artifact"]
            values[stage] = read_private(
                fetch_private(session, stage, response["artifact"], budget=budget), budget=budget
            )
            if stage == "cleanup":
                c2c_receipt = response["c2c_export"]
                cleanup_view, _ = fetch_export(
                    session, c2c_receipt, budget=budget, index_bytes=index_bytes
                )
                values[stage] = resolved_cleanup(
                    values[stage], cleanup_view, c2c_receipt["final"], rule["seal_id"], budget
                )
        cleanup, stopped, offline = (values[k] for k in ("cleanup", "stop", "offline"))
        if (
            cleanup["observer_closed_ns"] > stopped["stores_stopped_ns"]
            or offline["control"]["state"] != "shut down"
            or offline["control"]["system_identifier"]
            != cleanup["source_inventory"]["database"]["database_system_identifier"]
        ):
            raise ValueError("actual store/observer/control-data order or identity differs")
        if (
            offline["coverage"] != cleanup["storage"]["coverage"]
            or offline["coverage"]["root"]["serial"] != vm.plan.uuid.replace("-", "")[:20]
            or offline["used_build_digest"] != canonical_digest(offline["used_build"])
        ):
            raise ValueError("actual root/build identity differs")
        from scripts.execution_capacity.seal_storage import validate_coverage

        validate_coverage(
            offline["coverage"]["root"],
            offline["coverage"]["paths"],
            vm.plan.uuid.replace("-", "")[:20],
        )
        verify_live_vm(vm, qmp)
        stage = "guest-shutdown"
        stopped_ns = shutdown(vm, session.agent)
        stage = "offline-flatten"
        raw = flatten(vm, output, timeout=rule["phase_timeout_seconds"])
        mappings = []
        for role in ("os", "datastore", "objects", "redis"):
            matches = [p for p in offline["coverage"]["paths"] if p["role"] == role]
            if len(matches) != 1:
                raise ValueError("unique physical persistence mapping required")
            mappings.append(
                PersistenceMapping(
                    role=role,
                    root_relative_path=matches[0]["path"].lstrip("/") or ".",
                    filesystem_id=offline["coverage"]["root"]["major_minor"],
                    external=False,
                )
            )
        image = Image(
            image_id=raw["sha256"],
            kind="root",
            persistence=mappings,
            sha256=raw["sha256"],
            size_bytes=raw["size_bytes"],
            stopped_ns=stopped_ns,
            sealed_ns=time.monotonic_ns(),
            format="raw",
            device=raw["device"],
            inode=raw["inode"],
        )
        validate_images([image])
        metadata = cleanup["seal_metadata"]
        if any(rule["export"].get(k) != v for k, v in metadata.items()):
            raise ValueError("actual seal source metadata differs from immutable intent")
        public_values = {
            "schema_version": 3,
            "role": "seal",
            **rule["export"],
            "images": [image],
            "cohorts": cleanup["source_inventory"]["cohorts"],
        }
        if getattr(cleanup_view, "journal", None) is not None:
            from scripts.execution_capacity.public_record import public_record_value

            public_values = public_record_value(
                public_values, owner=cleanup_view.journal, budget=budget
            )
        public = Seal.model_validate(public_values)
        binding = {
            "seal_id": public.seal_id,
            "quiescence_digest": cleanup["writer_quiescence_digest"],
            "source_inventory_digest": cleanup["source_inventory_digest"],
            "image_digest": raw["sha256"],
        }
        private = {
            "seal": public.model_dump(),
            "writer_base": binding,
            "complete_quiescence_digest": cleanup["complete_quiescence_digest"],
            "used_build_digest": offline["used_build_digest"],
            "artifacts": receipts,
            "raw_identity": raw,
            "raw_path": str(output),
            "guest_identity": session.identity,
            "c2c_export": c2c_receipt,
            "config_digest": rule["config_digest"],
        }
        artifact = write_private(ledger.root / "sealed-base.json", private, budget=budget)
        ledger.append(
            "seal-complete",
            {
                **artifact,
                "image": raw,
                "writer_base": binding,
                "used_build_digest": offline["used_build_digest"],
                "c2c_export_digest": canonical_digest(c2c_receipt),
                "host_ns": time.monotonic_ns(),
            },
        )
        return VerifiedBase.open(ledger, budget=budget, index_bytes=index_bytes)
    except BaseException as error:
        ledger.append(
            "seal-failed",
            {
                "stage": stage,
                "type": type(error).__name__,
                "host_ns": time.monotonic_ns(),
                "images": "retained",
                "primary_and_cleanup": True,
            },
        )
        # Force-exit is a separate explicit recovery operation, never a clean
        # seal fallback. Guest helper retained all independent stop failures.
        raise
    finally:
        if cleanup_view is not None:
            cleanup_view.close()


@dataclass(frozen=True)
class VerifiedBase:
    private: dict
    seal: Seal
    image_stat: tuple
    artifact_digest: str
    ledger_root: Path
    retained_root: Path
    image_path: Path
    index_bytes: int | None = None

    @classmethod
    def open(cls, ledger, *, image_path=None, budget=None, index_bytes=None):
        from scripts.execution_capacity.c2c_export import _receipt, verify_export
        from scripts.execution_capacity.evidence_owner import EvidenceOwner

        budget = budget if budget is not None else EvidenceOwner().budget
        records = ledger.records("seal-complete")
        if (
            len(records) != 1
            or ledger.records("seal-failed")
            or ledger.records("qemu-force-exit-intent")
        ):
            raise ValueError("actual completed finalizer authority required")
        retained_root = getattr(ledger, "location", ledger.root)
        path = retained_root / "sealed-base.json"
        if _receipt(path, budget)["sha256"] != records[0]["body"]["sha256"]:
            raise ValueError("retained finalizer artifact changed")
        value = read_private(path, budget=budget)
        raw = Path(value["raw_path"] if image_path is None else image_path)
        # The immutable base image is an explicit store dependency; charge its
        # streaming working buffer, not its entire disk size as live memory.
        budget.reserve(min(raw.stat().st_size, 1024 * 1024), rows=0)
        if (
            hash_offline(raw) != value["raw_identity"]
            or value["raw_identity"] != records[0]["body"]["image"]
        ):
            raise ValueError("actual sealed raw identity differs")
        for phase, expected in value["artifacts"].items():
            actual = _receipt(retained_root / ("seal-guest-" + phase + ".json"), budget)
            if (
                actual["sha256"] != expected["sha256"]
                or actual["size_bytes"] != expected["size_bytes"]
            ):
                raise ValueError("retained original private inventory changed")
        rule = ledger.plan["seal"]
        if value.get("config_digest") != rule["config_digest"] or canonical_digest(
            value.get("c2c_export")
        ) != records[0]["body"].get("c2c_export_digest"):
            raise ValueError("original finalizer export linkage differs")
        view, _ = verify_export(
            retained_root,
            value["c2c_export"],
            protocol_id=rule["export"]["protocol_id"],
            config_digest=rule["config_digest"],
            identity=value["guest_identity"],
            budget=budget,
            index_bytes=index_bytes,
        )
        view.close()
        info = raw.stat()
        return cls(
            value,
            Seal.model_validate(value["seal"]),
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns),
            records[0]["body"]["sha256"],
            ledger.root,
            retained_root,
            raw,
            index_bytes,
        )

    def open_evidence(self, *, budget=None):
        """Reopen original ledger/base bytes before returning the full-final view."""
        from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger
        from scripts.execution_capacity.c2c_export import verify_export
        from scripts.execution_capacity.evidence_owner import EvidenceOwner

        budget = budget if budget is not None else EvidenceOwner().budget
        ledger = ReadOnlyAttemptLedger.open(
            self.retained_root, origin=self.ledger_root, budget=budget
        )
        reopened = type(self).open(
            ledger, image_path=self.image_path, budget=budget, index_bytes=self.index_bytes
        )
        if (
            reopened.artifact_digest != self.artifact_digest
            or reopened.private != self.private
            or reopened.seal != self.seal
        ):
            raise ValueError("actual reopened immutable base differs")
        view, guest = verify_export(
            self.retained_root,
            reopened.private["c2c_export"],
            protocol_id=reopened.seal.protocol_id,
            config_digest=reopened.private["config_digest"],
            identity=reopened.private["guest_identity"],
            budget=budget,
            index_bytes=self.index_bytes,
        )
        try:
            wire = read_private(self.retained_root / "seal-guest-cleanup.json", budget=budget)
            cleanup = resolved_cleanup(
                wire, view, reopened.private["c2c_export"]["final"], reopened.seal.seal_id, budget
            )
            roots = view.materialize()
            unit = reopened.private["c2c_export"]["final"]["unit"]
            if unit["seal_id"] != reopened.seal.seal_id or any(
                unit[key] != cleanup[key]
                for key in (
                    "source_inventory_digest",
                    "writer_quiescence_digest",
                    "complete_quiescence_digest",
                    "observer_closed_ns",
                )
            ):
                raise ValueError("original full-final unit commitments differ")
            return BaseOriginalEvidence(reopened, ledger, guest, view, roots)
        except BaseException:
            view.close()
            raise

    def clone_input(self):
        """Metadata-only startup join; full bytes were checked before measurement."""
        if (
            canonical_digest(self.private) != self.artifact_digest
            or self.seal.model_dump() != self.private["seal"]
        ):
            raise ValueError("immutable sealed binding changed")
        path = Path(self.private["raw_path"])
        info = path.lstat()
        if (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ) != self.image_stat:
            raise ValueError("sealed base changed before clone")
        return {
            "base": str(path),
            "base_identity": dict(self.private["raw_identity"]),
            "writer_base": dict(self.private["writer_base"]),
            "used_build_digest": self.private["used_build_digest"],
        }

    def load_history(self):
        """Read complete retained base history before measurements, never at cold startup."""
        from scripts.acceptance.capacity_models import Cohort
        from scripts.execution_capacity.inventory import SourceInventory

        self.clone_input()
        path = self.ledger_root / "seal-guest-cleanup.json"
        if hash_offline(path)["sha256"] != self.private["artifacts"]["cleanup"]["sha256"]:
            raise ValueError("original base writer history changed")
        value = read_private(path)
        validate_cleanup(value, self.seal.seal_id)
        source = dict(value["source_inventory"])
        source["cohorts"] = [Cohort.model_validate(c) for c in source["cohorts"]]
        return value["writer_base"], SourceInventory(**source).require_complete()


@dataclass(frozen=True)
class BaseOriginalEvidence:
    base: VerifiedBase
    host_ledger: object
    guest_ledger: object
    view: object
    roots: dict

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()

    def close(self):
        self.view.close()

    def descriptor(self):
        # Original locations stay private and are resolved by the later owned
        # source/copy context; they are never report-provided lookup paths.
        return {
            "origin": str(self.base.ledger_root),
            "sealed_artifact": self.base.artifact_digest,
            "seal_id": self.base.seal.seal_id,
            "protocol_id": self.base.seal.protocol_id,
            "image": self.base.private["raw_identity"],
            "export": self.base.private["c2c_export"],
            "manifest": self.view.manifest,
            "host_plan_sha256": self.host_ledger.plan_sha256,
            "host_ledger_sha256": self.host_ledger.ledger_sha256,
        }
