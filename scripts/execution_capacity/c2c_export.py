"""Actual guest originals closeout and host verification of its fixed ledger prefix."""

import os
from hashlib import sha256
from pathlib import Path

from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger, digest
from scripts.execution_capacity.evidence_files import stream_file
from scripts.execution_capacity.guest_seal_entry import artifact_path as artifact_path
from scripts.execution_capacity.original_shards import (
    OriginalView,
    _sync_directory,
    write_originals,
)
from scripts.execution_capacity.ownership import _open_private


def _receipt(path, budget):
    hashed = sha256()
    size = stream_file(path, budget=budget, consumer=hashed.update)
    return {"sha256": hashed.hexdigest(), "size_bytes": size}


def close_originals(root, config, ledger, roots, *, budget):
    protocol = config.get("protocol_id")
    if (
        not isinstance(protocol, str)
        or not protocol
        or ledger.plan.get("protocol_id") != protocol
        or ledger.plan.get("config_digest") != digest(config)
    ):
        raise ValueError("original protocol/config binding differs")
    with ledger.control_lock, ledger.thread_lock:
        if ledger.records("c2c-private-final") or ledger.poisoned:
            raise ValueError("original closeout already consumed or uncertain")
        prior = {
            "origin": str(ledger.root),
            "sequence": len(ledger.rows),
            "digest": ledger.chain,
            "bytes": os.fstat(ledger.fd).st_size,
        }
        cleanup = roots["cleanup"]
        unit = {
            "schema": 1,
            "kind": "base",
            "state": "complete",
            "phase": "after-exit",
            "seal_id": config["seal_id"],
            **{
                key: cleanup[key]
                for key in (
                    "source_inventory_digest",
                    "writer_quiescence_digest",
                    "complete_quiescence_digest",
                    "observer_closed_ns",
                )
            },
        }
        binding = {
            "protocol_id": protocol,
            "config_digest": digest(config),
            "identity": ledger.plan["identity"],
            "prior": prior,
            "unit": unit,
        }
        owner = getattr(ledger, "evidence_owner", None)
        if owner is not None and owner.journal is not None:
            if owner.budget is not budget or owner.journal.root != root / "c2c-originals":
                raise ValueError("actual original acquisition root/budget differs")
            manifest = owner.finish_originals(roots, binding)
            with OriginalView.open(
                root / "c2c-originals", budget=budget, index_bytes=owner.journal.index_bytes
            ) as view:
                if view.manifest != manifest:
                    raise ValueError("durable original closure changed")
        else:
            write_originals(root / "c2c-originals", roots, binding, budget=budget)
        receipt = _receipt(root / "c2c-originals" / "manifest.json", budget)
        final = {"manifest": receipt, **binding}
        ledger.append("c2c-private-final", final)
        return final


def snapshot_cleanup(root, ledger, final, *, budget):
    """Called after phase-complete under actual owner locks, before stop is allowed."""
    with ledger.control_lock, ledger.thread_lock:
        finals = ledger.records("c2c-private-final")
        if (
            ledger.poisoned
            or len(finals) != 1
            or finals[0]["body"] != final
            or not ledger.rows
            or ledger.rows[-1]["kind"] != "phase-complete"
            or ledger.rows[-1]["body"].get("phase") != "cleanup"
        ):
            raise ValueError("actual completed cleanup prefix required")
        target = root / "c2c-ledger"
        target.mkdir(mode=0o700)
        files = {}
        for name in ("plan.json", "attempt.jsonl"):
            with os.fdopen(
                _open_private(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
            ) as output:
                hashed = sha256()

                def consume(raw, hashed=hashed, output=output):
                    output.write(raw)
                    hashed.update(raw)

                size = stream_file(ledger.root / name, budget=budget, consumer=consume)
                output.flush()
                os.fsync(output.fileno())
            expected = {"sha256": hashed.hexdigest(), "size_bytes": size}
            if _receipt(target / name, budget) != expected:
                raise ValueError("copied cleanup prefix bytes differ")
            files[name] = expected
        _sync_directory(target)
        _sync_directory(root)
        return {
            "final": final,
            "origin": str(ledger.root),
            "sequence": len(ledger.rows),
            "digest": ledger.chain,
            "bytes": os.fstat(ledger.fd).st_size,
            "files": files,
        }


def verify_export(root, receipt, *, protocol_id, config_digest, identity, budget, index_bytes=None):
    """No authority from manifest alone: verify copied chain and exact final event."""
    if set(receipt) != {"final", "origin", "sequence", "digest", "bytes", "files"}:
        raise ValueError("invalid original export receipt")
    final = receipt["final"]
    if (
        set(final) != {"manifest", "protocol_id", "config_digest", "identity", "prior", "unit"}
        or final["protocol_id"] != protocol_id
        or final["config_digest"] != config_digest
        or final["identity"] != identity
    ):
        raise ValueError("original protocol/config/identity differs")
    unit = final["unit"]
    if (
        set(unit)
        != {
            "schema",
            "kind",
            "state",
            "phase",
            "seal_id",
            "source_inventory_digest",
            "writer_quiescence_digest",
            "complete_quiescence_digest",
            "observer_closed_ns",
        }
        or type(unit["schema"]) is not int
        or unit["schema"] != 1
        or unit["kind"] != "base"
        or unit["state"] != "complete"
        or unit["phase"] != "after-exit"
        or type(unit["observer_closed_ns"]) is not int
        or unit["observer_closed_ns"] < 1
    ):
        raise ValueError("invalid original base unit binding")
    if set(receipt["files"]) != {"plan.json", "attempt.jsonl"}:
        raise ValueError("exact original ledger files required")
    for name, expected in receipt["files"].items():
        if _receipt(root / "c2c-ledger" / name, budget) != expected:
            raise ValueError("copied original ledger bytes differ")
    if _receipt(root / "c2c-originals" / "manifest.json", budget) != final["manifest"]:
        raise ValueError("original manifest bytes differ")
    original = ReadOnlyAttemptLedger.open(
        root / "c2c-ledger", origin=Path(receipt["origin"]), budget=budget
    )
    if (
        original.plan
        != {"identity": identity, "config_digest": config_digest, "protocol_id": protocol_id}
        or len(original.rows) != receipt["sequence"]
        or original.chain != receipt["digest"]
        or receipt["files"]["attempt.jsonl"]["size_bytes"] != receipt["bytes"]
    ):
        raise ValueError("original plan/end boundary differs")
    events = original.records("c2c-private-final")
    if (
        len(events) != 1
        or events[0]["body"] != final
        or original.records("phase-error")
        or original.rows[-1]["kind"] != "phase-complete"
        or original.rows[-1]["body"].get("phase") != "cleanup"
    ):
        raise ValueError("original completed cleanup/final lineage differs")
    prior = final["prior"]
    if (
        set(prior) != {"origin", "sequence", "digest", "bytes"}
        or prior["origin"] != receipt["origin"]
        or prior["sequence"] + 1 != events[0]["sequence"]
        or prior["digest"] != events[0]["previous"]
    ):
        raise ValueError("original prior ledger boundary differs")
    offset = 0
    with os.fdopen(
        _open_private(root / "c2c-ledger" / "attempt.jsonl", os.O_RDONLY), "rb"
    ) as stream:
        for _ in range(prior["sequence"]):
            offset += len(stream.readline(budget.row_limit + 1))
    if offset != prior["bytes"]:
        raise ValueError("original prior byte boundary differs")
    view = OriginalView.open(root / "c2c-originals", budget=budget, index_bytes=index_bytes)
    if view.manifest["binding"] != {
        k: final[k] for k in ("protocol_id", "config_digest", "identity", "prior", "unit")
    }:
        view.close()
        raise ValueError("original manifest binding differs")
    return view, original


def fetch_export(session, receipt, *, budget, index_bytes=None):
    """Fetch all declared originals before the caller may request store shutdown."""
    from scripts.execution_capacity.guest_seal import read_private
    from scripts.execution_capacity.guest_seal_entry import original_artifacts
    from scripts.execution_capacity.seal_finalizer import fetch_private

    root = session.ledger.root
    (root / "c2c-originals").mkdir(mode=0o700)
    (root / "c2c-ledger").mkdir(mode=0o700)
    path = fetch_private(session, "c2c-manifest", receipt["final"]["manifest"], budget=budget)
    manifest = read_private(path, budget=budget)
    for name, descriptor in original_artifacts(manifest):
        budget.reserve(256, rows=1)
        fetch_private(session, name, descriptor, budget=budget)
    for name, logical in (("plan.json", "c2c-ledger-plan"), ("attempt.jsonl", "c2c-ledger-rows")):
        fetch_private(session, logical, receipt["files"][name], budget=budget)
    rule = session.ledger.plan["seal"]
    return verify_export(
        root,
        receipt,
        protocol_id=rule["export"]["protocol_id"],
        config_digest=rule["config_digest"],
        identity=session.identity,
        budget=budget,
        index_bytes=index_bytes,
    )
