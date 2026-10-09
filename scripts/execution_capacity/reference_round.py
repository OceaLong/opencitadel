"""Parent/physical-round authority. Does not create children or authorize reuse.

C3b schedules all global samples; C2c supplies actual cumulative cleanup and
empty delegated-group proof before any subsequent round may reuse resources.
"""

from pathlib import Path
from uuid import UUID

from scripts.acceptance.capacity_models import ID, Digest, Record, RoundOrigin
from scripts.execution_capacity.attempt import digest


class RoundBinding(Record):
    """Private original ledger wire shape, including the original child path."""

    parent_attempt_id: ID
    round_id: ID
    sample_id: ID
    window_id: ID
    parent_plan_digest: Digest
    child_plan_digest: Digest
    reservation_digest: Digest
    child_path: ID

    def safe(self):
        original = self.model_dump()
        return RoundOrigin(
            schema_version=1,
            **{key: value for key, value in original.items() if key != "child_path"},
            child_origin_sha256=digest(
                {
                    "domain": "opencitadel.capacity.child-origin.v1",
                    "origin": original,
                }
            ),
        )


def reserve_round(parent, child_plan, child_path, *, seal_digest):
    """Durably consume a global sample before the child exists; never retry it."""
    identity = child_plan["round"]
    if set(identity) != {"parent_attempt_id", "round_id", "sample_id", "window_id"}:
        raise ValueError("exact parent/round identity required")
    UUID(identity["round_id"])
    if identity["parent_attempt_id"] != parent.plan["attempt_id"]:
        raise ValueError("wrong parent attempt")
    expected = parent.root / "rounds" / identity["round_id"]
    if (
        Path(child_path) != expected
        or expected.exists()
        or expected.absolute() != expected.resolve()
    ):
        raise ValueError("fresh private exact round path required")
    with parent.control_lock:
        parent.bind_clock()
        if any(
            r["body"]["round_id"] == identity["round_id"] for r in parent.records("round-reserved")
        ):
            raise ValueError("round identity already consumed")
        if parent.records("round-reserved"):
            raise ValueError(
                "prior physical round retained; C2c cleanup/reuse gate not implemented"
            )
        reservation = parent.reserve(
            identity["sample_id"], identity["window_id"], seal_digest=seal_digest
        )
        binding = RoundBinding(
            **identity,
            parent_plan_digest=digest(parent.plan),
            child_plan_digest=digest(child_plan),
            reservation_digest=reservation,
            child_path=str(expected),
        )
        parent.append("round-reserved", binding.model_dump())
        return binding


def verify_round(parent, child):
    if parent is None:
        raise ValueError("actual parent reservation required before physical round")
    if parent.bind_clock() != child.bind_clock():
        raise ValueError("parent/round host clock domain differs")
    return verify_round_records(parent, child)


def verify_round_records(parent, child):
    """Offline record authority: no current clock reads, writes or path rewrites."""
    clocks = [ledger.records("host-clock") for ledger in (parent, child)]
    if any(len(rows) != 1 for rows in clocks) or clocks[0][0]["body"] != clocks[1][0]["body"]:
        raise ValueError("original parent/round host clock domain differs")
    clock = clocks[0][0]["body"]
    if (
        set(clock) != {"boot_id", "clock", "namespace_device", "namespace_inode"}
        or not isinstance(clock["boot_id"], str)
        or not clock["boot_id"]
        or clock["clock"] != "CLOCK_MONOTONIC"
        or any(
            type(clock[key]) is not int or clock[key] < 0
            for key in ("namespace_device", "namespace_inode")
        )
    ):
        raise ValueError("original host clock record incomplete")
    identity = child.plan["round"]
    rows = [
        r["body"]
        for r in parent.records("round-reserved")
        if r["body"]["round_id"] == identity["round_id"]
    ]
    if len(rows) != 1:
        raise ValueError("unique parent round reservation missing")
    binding = RoundBinding.model_validate(rows[0])
    if (
        binding.child_path != str(parent.root / "rounds" / identity["round_id"])
        or binding.parent_attempt_id != parent.plan["attempt_id"]
        or binding.parent_plan_digest != digest(parent.plan)
        or binding.child_plan_digest != digest(child.plan)
        or binding.child_path != str(child.root)
        or any(getattr(binding, k) != v for k, v in identity.items())
    ):
        raise ValueError("actual parent/round plan/path binding differs")
    reserved = [
        r
        for r in parent.records("reserved")
        if r["body"]["sample_id"] == binding.sample_id
        and r["body"]["window_id"] == binding.window_id
    ]
    if len(reserved) != 1 or digest(reserved[0]) != binding.reservation_digest:
        raise ValueError("original global sample reservation missing")
    return binding
