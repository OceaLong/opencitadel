"""Actual source inventory. Private complete identities precede safe exports.

No construction, acknowledgement, cleanup or success input is accepted here.
The caller owns verified deployment authority and the existing recovery journals.
A completed read is not writer quiescence or permission to seal a running disk.
"""

from dataclasses import dataclass, field
from pathlib import Path

from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_models import Cohort, Counts, RoundInventory

PUBLIC_READ_FIELDS = frozenset(
    {
        "name",
        "sql_digest",
        "parameter_digest",
        "start_ns",
        "end_ns",
        "rows",
        "result_digest",
        "error",
    }
)


def exact_owned_set(actual, expected, *, owner=None):
    if owner is not None:
        from hashlib import sha256

        from scripts.execution_capacity.attempt import encode
        from scripts.execution_capacity.predicate_maps import PredicateMap

        observed = PredicateMap(owner, "source_actual")
        wanted = PredicateMap(owner, "source_expected")
        for value in expected:
            if type(value) is not str:
                raise ValueError("complete actual owned source set differs")
            wanted[value] = None
        for value in actual:
            key = str(value)
            if key in observed:
                raise ValueError("complete actual owned source set differs")
            observed[key] = None
        if not observed.same_keys(wanted):
            raise ValueError("complete actual owned source set differs")
        hashed = sha256(b"[")
        for ordinal, (key, _) in enumerate(observed.sorted_items()):
            owner.budget.reserve(len(key) * 12 + 128, rows=0, largest=len(key) * 12 + 128)
            if ordinal:
                hashed.update(b",")
            hashed.update(encode(key))
        hashed.update(b"]")
        return hashed.hexdigest()
    actual = [str(x) for x in actual]
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        raise ValueError("complete actual owned source set differs")
    return canonical_digest(sorted(actual))


def batch_membership(attempts, judges, batches, *, owner=None):
    if owner is None:
        result, parents = {}, set()
    else:
        from scripts.execution_capacity.predicate_maps import PredicateMap

        result = PredicateMap(owner, "source_membership")
        parents = PredicateMap(owner, "source_parents")
    for kind, rows in (("evaluation_subject", attempts), ("evaluation_judge", judges)):
        for row in rows:
            batch, scope, run = str(row["batch_id"]), row["scope_key"], str(row["run_id"])
            key = (
                kind,
                str(row["result_id"]),
                row["attempt"] if kind == "evaluation_subject" else str(row["id"]),
            )
            if owner is not None:
                from scripts.execution_capacity.replay_relations import legacy_attempt_key

                key = legacy_attempt_key("parent", key, owner=owner, budget=owner.budget)
            if (
                batches.get(batch) != scope
                or not row["run_id"]
                or run in result
                or key in parents
                or (kind == "evaluation_subject" and row["intent"] is None)
            ):
                raise ValueError("missing/duplicate/foreign committed batch attempt parent")
            if owner is None:
                parents.add(key)
            else:
                parents[key] = None
            result[run] = (kind, scope, batch)
    return result


def read_build_inventory(root, groups, *, budget=None):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.evidence_files import file_digest

    budget = budget if budget is not None else EvidenceBudget(bytes_limit=32 * 1024 * 1024)
    if set(groups) != {"frontend", "dependencies", "ddl", "build"} or any(
        not v for v in groups.values()
    ):
        raise ValueError("complete actual build/frontend/dependency/DDL identities required")
    if len(groups["ddl"]) != 24 or len(set(groups["ddl"])) != 24:
        raise ValueError("exact 24 frozen DDL files required")
    root = Path(root).resolve()
    actual_ddl = {
        path.relative_to(root).as_posix() for path in (root / "api/alembic/versions").glob("*.py")
    }
    if actual_ddl != set(groups["ddl"]):
        raise ValueError("actual complete DDL inventory differs")
    hashes = {}
    for names in groups.values():
        for name in names:
            path = root / name
            if name in hashes or path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("duplicate or external build identity")
            budget.charge(name)
            hashes[name] = file_digest(path, budget=budget)
    return {"groups": groups, "files": hashes, "digest": canonical_digest(hashes)}


def validate_projectors(rows, scopes, *, owner=None):
    from app.infrastructure.execution.postgres_execution_view import (
        ALGORITHM_VERSION,
        SOURCE_VERSION,
    )

    if owner is None:
        complete = len(rows) == len(scopes) and {r["scope"] for r in rows} == set(scopes)
    else:
        from scripts.execution_capacity.predicate_maps import PredicateMap

        observed = PredicateMap(owner, "source_scopes")
        for row in rows:
            observed[row["scope"]] = None
        complete = len(rows) == len(scopes) and observed.same_keys(scopes)
    if not complete:
        raise ValueError("complete projector scope inventory differs")
    for row in rows:
        if (
            row["checkpoint"] is None
            or row["checkpoint"] < row["head"]
            or not row["generation"]
            or row["source_version"] != SOURCE_VERSION
            or row["algorithm_version"] != ALGORITHM_VERSION
        ):
            raise ValueError("stale or incompatible projector inventory")


@dataclass
class SourceInventory:
    """Private result retained even when a query or join fails."""

    database: dict = field(default_factory=dict)
    build: dict = field(default_factory=dict)
    owners: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    judges: list = field(default_factory=list)
    versions: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    runs: list = field(default_factory=list)
    cohorts: list = field(default_factory=list)
    projectors: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    reads_complete: bool = False
    safe_reads: list = field(default_factory=list)

    def require_complete(self):
        if not self.reads_complete or self.errors:
            raise ValueError("incomplete actual source inventory")
        return self

    def safe(self, *, owner=None, budget=None):
        if owner is not None:
            from scripts.execution_capacity.original_journal import OriginalJournal
            from scripts.execution_capacity.original_plain import canonical_original_digest

            if type(owner) is not OriginalJournal or budget is not owner.budget:
                raise ValueError("explicit source projection original owner/budget required")
            owner._usable()

            def digest_rows(rows):
                return canonical_original_digest(rows, owner=owner, budget=budget)
        else:
            digest_rows = canonical_digest
        # Original SQL operands and preflight controls stay in the private
        # inventory. Preserve the existing explicit public read-manifest fields.
        database = dict(self.database)
        if "reads" in database:
            if owner is not None:
                from scripts.execution_capacity.original_collections import (
                    BaseCollectionRows,
                    CollectionRows,
                    PlainBaseCollectionRows,
                    PlainCollectionRows,
                )

                if (
                    type(self.safe_reads)
                    not in (
                        CollectionRows,
                        PlainCollectionRows,
                        BaseCollectionRows,
                        PlainBaseCollectionRows,
                    )
                    or self.safe_reads.owner is not owner
                ):
                    raise ValueError("actual completed safe reads required")
                owner._collection_metadata(self.safe_reads)
                database["reads"] = self.safe_reads
            else:
                database["reads"] = [
                    {key: value for key, value in row.items() if key in PUBLIC_READ_FIELDS}
                    for row in database["reads"]
                ]
        return {
            "database": database,
            "build": self.build,
            "errors": self.errors,
            "reads_complete": self.reads_complete,
            "counts": {
                key: len(getattr(self, key))
                for key in (
                    "owners",
                    "attempts",
                    "judges",
                    "versions",
                    "objects",
                    "runs",
                    "projectors",
                )
            },
            "digests": {
                key: digest_rows(getattr(self, key))
                for key in (
                    "owners",
                    "attempts",
                    "judges",
                    "versions",
                    "objects",
                    "runs",
                    "projectors",
                )
            },
        }

    def safe_digest(self, *, owner=None, budget=None):
        value = self.safe(owner=owner, budget=budget)
        if owner is None:
            return canonical_digest(value)
        from scripts.execution_capacity.original_plain import canonical_original_digest

        return canonical_original_digest(value, owner=owner, budget=budget)

    def round_export(self, origin, base, *, owner=None, base_owner=None, budget=None):
        self.require_complete()
        if owner is not None:
            from scripts.execution_capacity.cohort_inventory import cohort_control, cohorts_equal
            from scripts.execution_capacity.evidence_json import json_digest
            from scripts.execution_capacity.predicate_maps import PredicateMap
            from scripts.execution_capacity.public_record import public_record_value

            owner._collection_metadata(self.cohorts)
            expected_base = iter(base)
            expected = PredicateMap(owner, "source_expected")
            for cohort in self.cohorts:
                control = cohort_control(cohort, owner=owner)
                if control.origin.kind == "base":
                    saved = next(expected_base, None)
                    if saved is None or not cohorts_equal(
                        [cohort], [saved], budget=budget, left_owner=owner, right_owner=base_owner
                    ):
                        raise ValueError("immutable base changed on physical clone")
                elif control.origin != origin:
                    raise ValueError("round source belongs to another child")
                for run_id in cohort["run_ids"]:
                    expected[run_id] = None
            if next(expected_base, None) is not None:
                raise ValueError("immutable base changed on physical clone")
            payload = {
                "origin": origin,
                "base_inventory_digest": json_digest(base, owner=base_owner, budget=budget),
                "cohorts": self.cohorts,
                "owned_run_count": len(self.owners),
                "owned_run_digest": exact_owned_set(
                    (r["stream_id"] for r in self.owners), expected, owner=owner
                ),
                "errors": [],
            }
            return RoundInventory.model_validate(
                public_record_value(payload, owner=owner, budget=budget, cohort_kind="round")
            )
        actual_base = [c for c in self.cohorts if c.origin.kind == "base"]
        if actual_base != base:
            raise ValueError("immutable base changed on physical clone")
        additions = [c for c in self.cohorts if c.origin.kind == "round"]
        if any(c.origin != origin for c in additions):
            raise ValueError("round source belongs to another child")
        actual = [r["stream_id"] for r in self.owners]
        expected = {r for c in self.cohorts for r in c.run_ids}
        return RoundInventory(
            origin=origin,
            base_inventory_digest=canonical_digest([c.model_dump() for c in base]),
            cohorts=additions,
            owned_run_count=len(actual),
            owned_run_digest=exact_owned_set(actual, expected),
            errors=[],
        )


def make_cohorts(membership, runs, origins, *, outputs=None):
    if outputs is not None:
        from scripts.execution_capacity.cohort_inventory import make_owned_cohorts

        return make_owned_cohorts(membership, runs, origins, outputs)
    groups = {}
    for run in runs:
        kind, scope, parent = membership[run["run_id"]]
        groups.setdefault((kind, scope, parent), []).append(run)
    result = []
    for (kind, scope, parent), records in sorted(groups.items()):
        counts = Counts(
            runs=len(records),
            formal_events=sum(r["formal_events"] for r in records),
            observations=sum(r["observations"] for r in records),
            visible_steps=sum(r["visible_steps"] for r in records),
        )
        result.append(
            Cohort(
                cohort_id=f"{kind}:{parent}",
                kind=kind,
                scope_id=scope,
                run_ids=sorted(r["run_id"] for r in records),
                source=counts,
                view=counts,
                parity_digest=canonical_digest(sorted(records, key=lambda r: r["run_id"])),
                origin=origins[parent],
            )
        )
    return result
