"""Private cohort grouping over every original run, retaining stable sort order."""

from hashlib import sha256

from pydantic import TypeAdapter
from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import Cohort, Counts
from scripts.acceptance.capacity_records import ID
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.original_dictionaries import key_identity
from scripts.execution_capacity.original_plain import canonical_parts
from scripts.execution_capacity.query_outputs import query_output

_RUN_ID = TypeAdapter(ID)


def make_owned_cohorts(membership, runs, origins, outputs):
    owner, parent = outputs.owner, outputs.parent
    owner._collection_metadata(runs)
    output = outputs.outputs["cohorts"]
    owner.budget.reserve(512, rows=1, largest=512)
    session = owner.index.append("source-cohort-sessions", b"{}")
    groups = "source-cohort-groups:" + str(session)
    for ordinal, run in enumerate(runs):
        run_id = run["run_id"]
        group = tuple(membership[run_id])
        if len(group) != 3:
            raise ValueError("original cohort group differs")
        # '/' precedes every hexadecimal codepoint digit: tuple prefix order
        # matches Python string ordering, including NUL and non-BMP strings.
        identity = "/".join(key_identity(owner, part) for part in group)
        saved = owner.index.find(groups, "identity", identity)
        if saved is None:
            group_ordinal = owner.index.count(groups)
            raw = encode([group, group_ordinal])
            owner.budget.reserve(
                len(raw) * 2 + len(identity), rows=1, largest=len(raw) + len(identity)
            )
            owner.index.append(groups, raw, keys={"identity": identity})
        else:
            saved_group, group_ordinal = strict_json(saved)
            if tuple(saved_group) != group:
                raise ValueError("original cohort group collision")
        member_key = key_identity(owner, run_id) + "/" + format(ordinal, "016x")
        owner.budget.reserve(len(member_key) + 256, rows=1, largest=len(member_key) + 256)
        owner.index.append(
            f"source-cohort-members:{session}:{group_ordinal}",
            encode([run_id, ordinal]),
            keys={"identity": member_key},
        )
    for position, raw in enumerate(owner.index.identity_rows(groups, "identity")):
        group, group_ordinal = strict_json(raw)
        kind, scope, parent_id = group
        if output.writer is None:
            if position >= len(output.expected):
                raise ValueError("original cohort count differs")
            members = query_output(owner, expected=output.expected[position]["run_ids"])
        else:
            members = query_output(
                owner,
                parent=parent,
                slot=f"source-cohort-members:{output.writer.token.ordinal}:{position}",
            )
        totals = dict.fromkeys(("runs", "formal_events", "observations", "visible_steps"), 0)
        digest = sha256(b"[")
        stream = f"source-cohort-members:{session}:{group_ordinal}"
        consumed = 0
        for member_raw in owner.index.identity_rows(stream, "identity"):
            run_id, ordinal = strict_json(member_raw)
            run = runs[ordinal]
            if run["run_id"] != run_id or list(membership[run_id]) != group:
                raise ValueError("original cohort member changed")
            _RUN_ID.validate_python(run_id)
            members.append(run_id)
            if consumed:
                digest.update(b",")
            for part in canonical_parts(run, owner=owner, budget=owner.budget):
                digest.update(part)
            totals["runs"] += 1
            for name in ("formal_events", "observations", "visible_steps"):
                totals[name] += run[name]
            consumed += 1
        if consumed != owner.index.count(stream):
            raise ValueError("original cohort member coverage differs")
        digest.update(b"]")
        counts = Counts(**totals)
        # Run IDs are independently validated with the exact public ID adapter;
        # every other Cohort / SourceOrigin / Counts validator executes here.
        control = Cohort(
            cohort_id=f"{kind}:{parent_id}",
            kind=kind,
            scope_id=scope,
            run_ids=[],
            source=counts,
            view=counts,
            parity_digest=digest.hexdigest(),
            origin=origins[parent_id],
        )
        row = control.model_dump()
        row["view"] = row["source"]
        row["run_ids"] = members.complete()
        output.append(row)
    return output.complete()


def cohort_control(row, *, owner=None):
    """Validate the original public control and every ID without collecting IDs."""
    if type(row) is Cohort:
        return row
    if type(row) is not dict or set(row) != set(Cohort.model_fields):
        raise ValueError("complete original cohort fields required")
    from scripts.execution_capacity.original_collections import (
        BaseCollectionRows,
        CollectionRows,
        PlainBaseCollectionRows,
        PlainCollectionRows,
    )

    members = row["run_ids"]
    if (
        type(members)
        not in (CollectionRows, PlainCollectionRows, BaseCollectionRows, PlainBaseCollectionRows)
        or members.owner is not owner
    ):
        raise ValueError("actual original cohort member owner required")
    owner._collection_metadata(members)
    for run_id in members:
        _RUN_ID.validate_python(run_id)
    return Cohort.model_validate({**row, "run_ids": []})


def cohorts_equal(left, right, *, budget, left_owner=None, right_owner=None):
    from scripts.execution_capacity.evidence_json import chunks, equal_streams

    return equal_streams(
        chunks(left, owner=left_owner, budget=budget),
        chunks(right, owner=right_owner, budget=budget),
    )
