"""Immutable base writer history, bound to actual parent/child/image authority.

C2c4 binds the exported digest to the offline seal; C3 persists that exact binding
in parent and child plans. A standalone caller-authored clean JSON never suffices.
"""

from copy import deepcopy
from dataclasses import dataclass

from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.reference_round import verify_round


def export_writer_base(quiescence, seal_id, *, owner=None, budget=None):
    quiescence.require_complete()
    final = quiescence.final
    final["source"].require_complete()
    observed = final["writers"]
    payload = {
        "seal_id": seal_id,
        "source_inventory_digest": final["source"].safe_digest(owner=owner, budget=budget),
        "writers": observed["writers"],
        "uploads": observed["uploads"],
        "supervisors": observed["supervisors"],
        "exits": quiescence.writers,
        "errors": quiescence.errors,
    }
    _complete(payload)
    return payload


def _complete(payload):
    if (
        payload["errors"]
        or not payload["writers"]
        or not payload["exits"]
        or any(r.get("exited") is not True for r in payload["exits"])
    ):
        raise ValueError("prior after-exit quiescence incomplete")
    if set(payload["writers"]) != set(payload["supervisors"]):
        raise ValueError("prior writer supervisor set incomplete")
    for key, writer in payload["writers"].items():
        if writer["receipt"] is None or not writer["receipt"].get("resource_closed"):
            raise ValueError("prior writer not drained")
        if any(
            r["state"] not in {"completed", "cancelled"} or r["error"] is not None
            for r in payload["supervisors"][key]["body"]["reports"]
        ):
            raise ValueError("prior supervisor uncertainty retained")
    if any(
        r["receipt"] is None or r["body"]["writer_id"] not in payload["writers"]
        for r in payload["uploads"].values()
    ):
        raise ValueError("prior upload uncertainty retained")


@dataclass(frozen=True)
class SealedWriters:
    writers: dict
    uploads: dict
    supervisors: dict
    digest: str
    seal_id: str

    @classmethod
    def from_ledgers(cls, payload, parent, child, base_inventory, *, owner=None, budget=None):
        verify_round(parent, child)
        base_inventory.require_complete()
        try:
            expected = parent.plan["writer_base"]
            if (
                child.plan["writer_base"] != expected
                or set(expected)
                != {"seal_id", "quiescence_digest", "source_inventory_digest", "image_digest"}
                or child.plan["vm"]["base_identity"]["sha256"] != expected["image_digest"]
                or payload["seal_id"] != expected["seal_id"]
                or canonical_digest(payload) != expected["quiescence_digest"]
                or (
                    canonical_digest(base_inventory.safe())
                    if owner is None
                    else base_inventory.safe_digest(owner=owner, budget=budget)
                )
                != expected["source_inventory_digest"]
                or payload["source_inventory_digest"] != expected["source_inventory_digest"]
            ):
                raise ValueError("actual base writer/image/source linkage differs")
            _complete(payload)
        except (KeyError, TypeError) as error:
            raise ValueError("sealed writer authority missing") from error
        return cls(
            deepcopy(payload["writers"]),
            deepcopy(payload["uploads"]),
            deepcopy(payload["supervisors"]),
            expected["quiescence_digest"],
            expected["seal_id"],
        )
