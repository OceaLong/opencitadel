"""Concrete after-exit round export owner; C3b remains the runtime scheduler."""

import os
from contextlib import contextmanager
from hashlib import sha256

from scripts.execution_capacity.attempt import AttemptLedger, ReadOnlyAttemptLedger
from scripts.execution_capacity.cumulative_cleanup import quiesce
from scripts.execution_capacity.evidence_json import json_digest
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.final_inventory import FinalInventory
from scripts.execution_capacity.original_shards import OriginalView, write_originals
from scripts.execution_capacity.reference_round import verify_round_records
from scripts.execution_capacity.seal_finalizer import VerifiedBase


class OwnedRoundOriginals:
    def __init__(self, parent, child, base, *, resources, final):
        if (
            type(parent) is not AttemptLedger
            or type(child) is not AttemptLedger
            or type(base) is not VerifiedBase
            or type(final) is not FinalInventory
            or type(resources.evidence) is not EvidenceOwner
        ):
            raise TypeError("concrete original round owners required")
        if (
            final.reader.evidence is not resources.evidence
            or final.docker is not resources.evidence_transport
            or final.reader.storage is not resources.evidence_objects
        ):
            raise ValueError("round observer ownership differs")
        if resources.closed_ns is not None:
            raise ValueError("fresh open round observer required")
        self.parent, self.child, self.base = parent, child, base
        self.resources, self.final, self.owner = resources, final, resources.evidence
        self.result, self.consumed = None, False
        self.binding, base_original = self._verify_owners()
        with base_original:
            self.base_descriptor = base_original.descriptor()
        if final._round_originals is not None:
            raise ValueError("round final observer already has an owner")
        final._round_originals = self
        if child.records("c2c-round-collection-intent") or child.records("c2c-private-final"):
            raise ValueError("round original collection already consumed")

    def _verify_owners(self):
        snapshots = []
        for owner in (self.parent, self.child):
            # Only actual open owner handles; reread bytes rather than trusting
            # mutable rows/by_kind or a copied dataclass constructor.
            with owner.control_lock, owner.thread_lock:
                if (
                    owner.poisoned
                    or os.fstat(owner.fd).st_ino != (owner.root / "attempt.jsonl").stat().st_ino
                ):
                    raise ValueError("actual round ledger owner changed")
                view = ReadOnlyAttemptLedger.open(
                    owner.root, origin=owner.root, budget=self.owner.budget
                )
                if (
                    view.plan != owner.plan
                    or view.chain != owner.chain
                    or len(view.rows) != len(owner.rows)
                ):
                    raise ValueError("actual round ledger bytes differ")
                snapshots.append(view)
        parent, child = snapshots
        base = self.base.open_evidence(budget=self.owner.budget)
        try:
            return verify_round_lineage(
                parent,
                child,
                base,
                base.roots["cleanup"],
                inherited=self.final.base,
                writers={
                    name: getattr(self.final.writers.base, name)
                    for name in ("writers", "uploads", "supervisors")
                },
                origin=self.final.reader.origin,
                budget=self.owner.budget,
                owner=self.owner.journal,
            )
        except BaseException:
            base.close()
            raise

    @contextmanager
    def verified_base_evidence(self):
        # No saved clean flag or naked final dictionary authorizes deleted
        # history. Reopen actual owners/base and exact membership for this round.
        binding, base = self._verify_owners()
        with base:
            if binding != self.binding or base.descriptor() != self.base_descriptor:
                raise ValueError("round original base history binding changed")
            yield base

    async def collect(self, *, workloads, diagnostics, uploads):
        if self.consumed or self.result is not None:
            raise ValueError("round original collection already consumed")
        self.consumed = True
        with self.child.control_lock, self.child.thread_lock:
            if self.child.records("c2c-round-collection-intent"):
                raise ValueError("round original collection already consumed")
            self.child.append(
                "c2c-round-collection-intent",
                {"round": self.binding.model_dump(), "base_artifact": self.base.artifact_digest},
            )
        self.result = await quiesce(
            writers=self.final.writers,
            workloads=workloads,
            diagnostics=diagnostics,
            uploads=uploads,
            final=self.final,
        )
        return self.result

    def finish(self):
        if (
            self.result is None
            or self.resources.closed_ns is None
            or self.resources.closed_ns < self.result.ended_ns
        ):
            raise ValueError("actual collected round and observer closure required")
        try:
            self.result.require_complete()
        except BaseException as error:
            self.owner.retain_failure(self.child.root, error, resources=self.resources)
            raise
        with self.child.control_lock, self.child.thread_lock:
            if self.child.records("c2c-private-final"):
                raise ValueError("round original export already consumed")
            try:
                return self._finish_checked()
            except BaseException as error:
                if not (self.child.root / "c2c-failed-originals.json").exists():
                    self.owner.retain_failure(self.child.root, error, resources=self.resources)
                raise

    def _finish_checked(self):
        binding, base = self._verify_owners()
        with base:
            return self._finish_base_checked(binding, base)

    def _finish_base_checked(self, binding, base):
        if binding != self.binding or base.descriptor() != self.base_descriptor:
            raise ValueError("round/base original membership changed")
        budget = self.owner.budget
        commitments = {
            "full_quiescence": json_digest(self.result, budget=budget, owner=self.owner.journal),
            "source_safe": json_digest(
                self.result.final["source"].safe(owner=self.owner.journal, budget=budget),
                budget=budget,
                owner=self.owner.journal,
            ),
        }
        prior = {
            "origin": str(self.child.root),
            "sequence": len(self.child.rows),
            "digest": self.child.chain,
            "bytes": os.fstat(self.child.fd).st_size,
        }
        context = {
            "schema": 1,
            "kind": "round",
            "state": "complete",
            "phase": "after-exit",
            "protocol_id": self.base.seal.protocol_id,
            "round": self.binding.model_dump(),
            "base": base.descriptor(),
            "prior": prior,
            "commitments": commitments,
            "observer_closed_ns": self.resources.closed_ns,
        }
        root = self.child.root / "c2c-originals"
        try:
            roots = {
                "cleanup": self.result,
                "operands": self.owner.originals,
                "objects": self.resources.evidence_objects.originals,
                "transports": self.resources.evidence_transport.originals,
                "sql": self.owner.sql_reads,
            }
            if self.owner.journal is not None:
                if self.owner.journal.root != root:
                    raise ValueError("actual round acquisition root differs")
                manifest = self.owner.finish_originals(roots, context)
            else:
                manifest = write_originals(root, roots, context, budget=budget)
        except BaseException as error:
            self.owner.retain_failure(self.child.root, error, resources=self.resources)
            raise
        from scripts.execution_capacity.evidence_files import stream_file

        hashed = sha256()
        size = stream_file(root / "manifest.json", budget=budget, consumer=hashed.update)
        final = {
            "binding": context,
            "manifest": {"sha256": hashed.hexdigest(), "size_bytes": size},
        }
        view = OriginalView.open(
            root,
            budget=budget,
            index_bytes=None if self.owner.journal is None else self.owner.journal.index_bytes,
            verified_base=base if self.owner.journal is not None else None,
        )
        if view.manifest != manifest:
            view.close()
            raise ValueError("round durable original manifest changed")
        self.child.append("c2c-private-final", final)
        return view


def verify_round_lineage(
    parent, child, base, original_cleanup, *, inherited, writers, origin, budget, owner=None
):
    binding = verify_round_records(parent, child)
    expected = base.base.private["writer_base"]
    from scripts.execution_capacity.evidence_json import chunks, equal_streams

    base_owner = getattr(base.view, "journal", None)
    if len(inherited) != 1 or not equal_streams(
        chunks(inherited[0], budget=budget, owner=owner),
        chunks(original_cleanup["source_inventory"], budget=budget, owner=base_owner),
    ):
        raise ValueError("round exact inherited base membership differs")
    for family in ("writers", "uploads", "supervisors"):
        if writers[family] != original_cleanup["writer_base"][family]:
            raise ValueError("round original inherited writer rows differ")
    vm = child.plan["vm"]
    if (
        parent.plan["writer_base"] != expected
        or child.plan["writer_base"] != expected
        or vm["base_identity"] != base.base.private["raw_identity"]
        or vm["base"] != base.base.private["raw_path"]
        or parent.plan["protocol_id"] != base.base.seal.protocol_id
        or child.plan["protocol_id"] != base.base.seal.protocol_id
    ):
        raise ValueError("round actual base/writer/protocol differs")
    for ledger in (parent, child):
        matches = [
            r
            for r in ledger.records("reserved")
            if r["body"]["sample_id"] == binding.sample_id
            and r["body"]["window_id"] == binding.window_id
        ]
        if len(matches) != 1 or matches[0]["body"]["seal_digest"] != expected["image_digest"]:
            raise ValueError("round exact sealed sample reservation differs")
    intents, clones = child.records("overlay-create-intent"), child.records("overlay-created")
    if len(intents) != 1 or len(clones) != 1:
        raise ValueError("round unique original clone required")
    intent, clone = intents[0]["body"], clones[0]["body"]
    chain = clone["chain"]
    if (
        intent != {"uuid": vm["uuid"], "base": vm["base"], "overlay": vm["overlay"]}
        or clone["uuid"] != vm["uuid"]
        or intents[0]["sequence"] >= clones[0]["sequence"]
        or len(chain) != 2
        or chain[0]["format"] != "qcow2"
        or chain[1]["format"] != "raw"
        or chain[0]["full-backing-filename"] != vm["base"]
        or chain[1]["filename"] != vm["base"]
        or chain[0]["virtual-size"] != chain[1]["virtual-size"]
    ):
        raise ValueError("round original clone/base chain differs")
    discovered = child.records("guest-discovered")
    if len(discovered) != 1:
        raise ValueError("round unique original guest discovery required")
    identity = discovered[0]["body"]["identity"]
    intent = {k: v for k, v in identity.items() if k != "boot_id"}
    if (
        intent not in child.plan.get("guest_sessions", [])
        or intent.get("sample_id") != binding.sample_id
        or intent.get("window_id") != binding.window_id
        or not any(
            r["body"]["identity"] == intent and r["sequence"] < discovered[0]["sequence"]
            for r in child.records("guest-discovery-intent")
        )
    ):
        raise ValueError("round original guest immutable intent differs")
    if (
        len(discovered) != 1
        or origin.kind != "round"
        or origin.round != binding.safe()
        or origin.seal_id != expected["seal_id"]
        or origin.clone_id != vm["uuid"]
        or origin.boot_id != discovered[0]["body"]["identity"]["boot_id"]
        or discovered[0]["body"]["identity"]["attempt_id"] != binding.round_id
    ):
        raise ValueError("round original guest/cohort origin differs")
    return binding, base
