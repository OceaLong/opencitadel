"""Closed source inventory replay over exact owner bundles and finite query ports."""

from dataclasses import fields
from uuid import UUID

from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_models import SourceOrigin
from scripts.execution_capacity.evidence_owner import FAMILIES
from scripts.execution_capacity.inventory import SourceInventory
from scripts.execution_capacity.inventory_reader import (
    SourceInventoryReader,
    collect_source,
    read_dataset_objects,
)
from scripts.execution_capacity.inventory_sql import plain, validate_preflight
from scripts.execution_capacity.retained_run import RetainedRunInputs, _literal
from scripts.execution_capacity.retained_versions import RetainedVersionWork, typed
from sqlalchemy import text

from app.domain.analysis.metrics import METRIC_VERSION


class OriginalTrace:
    def __init__(self, roots, *, budget, owner=None):
        if set(roots["operands"]) != FAMILIES:
            raise ValueError("original trace families incomplete")
        self.roots, self.budget = roots, budget
        if owner is not None:
            from scripts.execution_capacity.original_journal import OriginalJournal

            if type(owner) is not OriginalJournal or not owner.owns_sequence(roots["sql"], "sql"):
                raise ValueError("actual original replay owner required")
            owner._usable()
        self.owner = owner
        self.positions = dict.fromkeys(FAMILIES, 0)
        self.sql_position = self.object_position = 0
        self.relations = None
        if owner is not None:
            from scripts.execution_capacity.replay_relations import ReplayRelations

            self.relations = ReplayRelations(owner, roots, budget)
        else:
            self.snapshots, self.parent_values, self.principals = {}, {}, {}

    def set_principal(self, scope, value):
        if self.relations is not None:
            self.relations.set_principal(scope, value)
        else:
            self.principals[scope] = value

    def principal(self, scope):
        return (
            self.relations.principal(scope)
            if self.relations is not None
            else self.principals.get(scope)
        )

    def plain_value(self, value):
        if self.owner is None:
            return plain(value)
        from scripts.execution_capacity.original_plain import plain_graph

        return plain_graph(value, owner=self.owner, budget=self.budget)

    def plain_digest(self, value):
        if self.owner is None:
            return canonical_digest(plain(value))
        from scripts.execution_capacity.original_plain import canonical_original_digest

        return canonical_original_digest(
            self.plain_value(value), owner=self.owner, budget=self.budget
        )

    def plain_equal(self, left, right):
        if self.owner is None:
            return plain(left) == plain(right)
        from scripts.execution_capacity.original_imports import equal_values

        equal_values(
            self.plain_value(left), self.owner, self.plain_value(right), self.owner, self.budget
        )
        return True

    def take(self, family):
        index = self.positions[family]
        rows = self.roots["operands"][family]
        if index == len(rows):
            raise ValueError("original operand absent")
        self.positions[family] += 1
        return rows[index]

    def dispatch(self, statement, parameters=None):
        if self.sql_position == len(self.roots["sql"]):
            raise ValueError("original SQL absent")
        row = self.roots["sql"][self.sql_position]
        self.sql_position += 1
        compiled = statement.compile()
        if (
            row.get("statement") != str(compiled)
            or row.get("parameters") != (parameters or {})
            or row.get("bound_parameters") != compiled.params
            or row.get("error") is not None
            or row.get("dispatched") is not True
            or not 0 < row["start_ns"] <= row["end_ns"]
            or row["snapshot"] != row["preflight"]["snapshot"]
        ):
            raise ValueError("original SQL dispatch/operands differ")
        if self.relations is not None:
            self.relations.snapshot(self.sql_position - 1, row)
        elif self.snapshots.setdefault(row["uow"], row["snapshot"]) != row["snapshot"]:
            raise ValueError("original UOW snapshot differs")
        validate_preflight(row["preflight"], self.budget)
        self.budget.reserve(
            row["preflight"]["total_bytes"] * 64 + row["preflight"]["row_count"] * 4096,
            rows=row["preflight"]["total_bytes"] + row["preflight"]["row_count"] * 16,
        )
        return row

    def begin(self, bundle, family):
        if (
            set(bundle) != {"identity", "ranges", "sql", "objects", "error"}
            or bundle["error"] is not None
            or set(bundle["ranges"]) != FAMILIES - {family}
        ):
            raise ValueError("original source bundle incomplete")
        for name, bounds in bundle["ranges"].items():
            if (
                type(bounds) is not list
                or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or bounds[0] != self.positions[name]
                or not bounds[0] <= bounds[1] <= len(self.roots["operands"][name])
            ):
                raise ValueError("original bundle family sequence differs")
        for bounds, start, rows in (
            (bundle["sql"], self.sql_position, self.roots["sql"]),
            (bundle["objects"], self.object_position, self.roots["objects"]),
        ):
            if (
                type(bounds) is not list
                or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or bounds[0] != start
                or not bounds[0] <= bounds[1] <= len(rows)
            ):
                raise ValueError("original bundle read sequence differs")

    def end(self, bundle):
        if (
            any(self.positions[name] != bounds[1] for name, bounds in bundle["ranges"].items())
            or self.sql_position != bundle["sql"][1]
            or self.object_position != bundle["objects"][1]
        ):
            raise ValueError("original bundle not fully consumed")

    def consumed(self, bundle):
        for name, bounds in bundle["ranges"].items():
            self.positions[name] = bounds[1]
        self.sql_position = bundle["sql"][1]
        self.object_position = bundle["objects"][1]

    def reserve_state(self, items, *, bytes_per_item=256):
        self.budget.reserve(items * bytes_per_item, rows=items)

    async def get_bytes(self, key):
        if self.object_position == len(self.roots["objects"]):
            raise ValueError("original source object absent")
        row = self.roots["objects"][self.object_position]
        self.object_position += 1
        if (
            row["key"] != key
            or row["error"] is not None
            or type(row["data"]) is not bytes
            or not 0 < row["start_ns"] <= row["end_ns"]
        ):
            raise ValueError("original source object differs")
        self.budget.reserve(len(row["data"]) * 64, rows=1)
        return row["data"]

    def parent_read(self, operation, kind, key):
        row = self.take("journal-read")
        if row["sequence"] != self.positions["journal-read"] or (
            row["operation"],
            row["kind"],
            row["key"],
            row["error"],
            row["sql_boundary"],
        ) != (operation, kind, key, None, self.sql_position):
            raise ValueError("original parent read identity/sequence differs")
        values = row["value"] if operation == "records" else [(key, row["value"])]
        previous_identity = None
        for pair, (identity, value) in enumerate(values):
            if operation == "records":
                if type(identity) is not str or (
                    previous_identity is not None and identity <= previous_identity
                ):
                    raise ValueError("original parent enumeration order differs")
                previous_identity = identity
            if value is not None and set(value) != {"body", "receipt"}:
                raise ValueError("original parent body/receipt missing")
            if self.relations is not None:
                self.relations.parent(
                    kind,
                    identity,
                    self.positions["journal-read"] - 1,
                    pair if operation == "records" else None,
                    value,
                )
            else:
                cache = (kind, identity)
                if cache in self.parent_values and self.parent_values[cache] != value:
                    raise ValueError("repeated original parent changed")
                self.parent_values[cache] = value
        return row["value"]

    def get(self, kind, key):
        return self.parent_read("get", kind, str(key))

    def parent(self, kind, key):
        row = self.get(kind, key)
        if row is None:
            raise ValueError("original parent missing")
        return row["body"]

    def records(self, kind):
        return self.parent_read("records", kind, None)


class RetainedInventoryQueries:
    def __init__(self, trace, *, expected_reads=None):
        self.trace, self.reads, self.uow = trace, [], None
        self._outputs_finished = False
        if trace.owner is not None:
            from scripts.execution_capacity.query_outputs import query_output

            self.reads = query_output(trace.owner, expected=expected_reads)

    def finish_outputs(self):
        if not self._outputs_finished:
            if self.trace.owner is not None:
                self.reads = self.reads.complete()
            self._outputs_finished = True
        return self.reads

    async def rows(self, name, sql, params=None):
        if self._outputs_finished:
            raise ValueError("query output is not accepting queries")
        params = params or {}
        index = self.trace.sql_position
        observed = self.trace.dispatch(text(sql), params)
        if self.uow is None:
            self.uow = observed["uow"]
        if observed["uow"] != self.uow:
            raise ValueError("original inventory UOW changed")
        original = self.trace.take("query-rows")
        if set(original) != {"name", "rows", "statement", "parameters", "sql_index", "read"} or (
            original["name"],
            original["statement"],
            original["parameters"],
            original["sql_index"],
        ) != (name, sql, params, index):
            raise ValueError("original inventory query differs")
        record, rows = original["read"], original["rows"]
        if self.trace.owner is not None:
            from scripts.execution_capacity.original_plain import canonical_original_digest

            result_digest = canonical_original_digest(
                plain(rows), owner=self.trace.owner, budget=self.trace.budget
            )
        else:
            result_digest = canonical_digest(plain(rows))
        if (
            record.get("error") is not None
            or record["parameters"] != plain(params)
            or record["sql_digest"] != canonical_digest(sql)
            or record["parameter_digest"] != canonical_digest(plain(params))
            or record["preflight"] != observed["preflight"]
            or record["rows"] != len(rows)
            or len(rows) != observed["preflight"]["row_count"]
            or record["result_digest"] != result_digest
            or not 0
            < record["start_ns"]
            <= observed["start_ns"]
            <= observed["end_ns"]
            <= record["end_ns"]
        ):
            raise ValueError("original inventory query result/read differs")
        self.reads.append(record)
        return rows


class RetainedSourceReader(SourceInventoryReader):
    def __init__(self, trace, *, signing_secret, cursor_secret):
        self.trace, self.signing_secret, self.cursor_secret = trace, signing_secret, cursor_secret
        self.evidence = self.parents = self.storage = trace

    async def replay(self, expected, *, origin, base=()):
        index = self.trace.positions["source-input"]
        if index == len(self.trace.roots["operands"]["source-input"]):
            raise ValueError("original source bundle absent")
        bundle = self.trace.roots["operands"]["source-input"][index]
        self.trace.begin(bundle, "source-input")
        identity = bundle["identity"]
        if (
            set(identity) != {"binding", "seed", "origin", "build_groups"}
            or SourceOrigin.model_validate(identity["origin"]) != origin
        ):
            raise ValueError("original source identity differs")
        self.binding, self.seed, self.origin = identity["binding"], identity["seed"], origin
        build = expected["build"]
        if (
            self.binding.get("environment") != "test"
            or build["groups"] != identity["build_groups"]
            or build["digest"] != canonical_digest(build["files"])
            or build["digest"] != self.binding["inventory_build_digest"]
            or build["source_digest"] != self.binding["source_sha256"]
            or build["metric_version"] != METRIC_VERSION
        ):
            raise ValueError("original source build/binding differs")
        if set(expected) != {field.name for field in fields(SourceInventory)}:
            raise ValueError("original source result coverage differs")
        result = SourceInventory(build=build)
        outputs = None
        if self.trace.owner is not None:
            from scripts.execution_capacity.source_outputs import SourceOutputs

            outputs = SourceOutputs(result, self.trace.owner, expected=expected)
        query = RetainedInventoryQueries(self.trace, expected_reads=expected["database"]["reads"])
        await collect_source(self, query, result, base, outputs=outputs)
        if outputs is not None:
            outputs.close()
        result.require_complete()
        if self.trace.owner is not None:
            from scripts.execution_capacity.evidence_owner import copy_original
            from scripts.execution_capacity.original_imports import equal_values
            from scripts.execution_capacity.original_plain import plain_graph

            actual = plain_graph(
                copy_original(result.__dict__, budget=self.trace.budget, owner=self.trace.owner),
                owner=self.trace.owner,
                budget=self.trace.budget,
            )
            expected_plain = plain_graph(expected, owner=self.trace.owner, budget=self.trace.budget)
            equal_values(
                actual, self.trace.owner, expected_plain, self.trace.owner, self.trace.budget
            )
        elif plain(typed(result.__dict__)) != plain(expected):
            raise ValueError("replayed original source result differs")
        self.trace.end(bundle)
        self.trace.take("source-input")
        return result

    async def _versions(self, query, result, batch, parent):
        bundle = self.trace.take("version-input")
        self.trace.begin(bundle, "version-input")
        identity = bundle["identity"]
        if (
            identity["batch"] != batch
            or identity["parent"] != parent
            or identity["principal"] != self.trace.principal(batch["scope_key"])
        ):
            raise ValueError("original version batch parent differs")
        replay = RetainedVersionWork.from_bundle(
            bundle,
            self.trace.roots["operands"],
            self.trace.roots["sql"],
            self.trace.roots["objects"],
            cursor_secret=self.cursor_secret,
            budget=self.trace.budget,
        )
        dataset = await replay.replay(result)
        self.trace.consumed(bundle)
        await read_dataset_objects(query, self.storage, result, batch, dataset)

    async def _run(self, query, run, kind, scope, parent, live_parents, admitted_parents):
        from scripts.execution_capacity.batch_facts import BatchFacts, own_run_parent

        if kind in {"evaluation_subject", "evaluation_judge"}:
            self.parents.parent("live_batch" if parent in live_parents else "batch", UUID(parent))
            for key, row in self.parents.records("lease"):
                if row["receipt"] is not None:
                    UUID(key)
            params = {"scope": scope, "run": UUID(run), "batch": UUID(parent)}
            for prefix in ("SELECT a.result_id", "SELECT id,result_id,candidate"):
                observed = self.trace.dispatch(_literal(BatchFacts.own_run, prefix), params)
                if observed["uow"] != query.uow:
                    raise ValueError("original batch parent UOW differs")
            raw = self.trace.take("batch-source")
            if set(raw) != {"operation", "scope", "batch_id", "run_id", "subjects", "judges"} or (
                raw["operation"],
                raw["scope"],
                raw["batch_id"],
                raw["run_id"],
            ) != ("batch.own_run", scope, UUID(parent), UUID(run)):
                raise ValueError("original batch Run parent differs")
            own_run_parent(raw["subjects"], raw["judges"], scope, UUID(parent))
        bundle = self.trace.take("run-input")
        self.trace.begin(bundle, "run-input")
        if bundle["identity"] != {"run_id": run, "scope": scope, "kind": kind}:
            raise ValueError("original source Run identity differs")
        replay = RetainedRunInputs(
            bundle,
            self.trace.roots["operands"],
            self.trace.roots["sql"],
            self.trace.roots["objects"],
            signing_secret=self.signing_secret,
            budget=self.trace.budget,
        )
        result = await replay.replay()
        if replay.uow != query.uow:
            raise ValueError("original source Run snapshot differs")
        query.reads.extend(row["read"] for row in replay.families["query-rows"])
        self.trace.consumed(bundle)
        return result
