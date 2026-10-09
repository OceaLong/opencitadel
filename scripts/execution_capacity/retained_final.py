"""Original final predicates, tied to full read/transport/journal sequences."""

import json
from dataclasses import fields
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import SourceOrigin
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.batch_facts import (
    ATTEMPT_SQL,
    LEASE_SQL,
    OPERATION_SQL,
    lease_observation_clean,
)
from scripts.execution_capacity.broker_inventory import (
    expected_requests,
    read_broker,
    reconcile,
    request_parents,
)
from scripts.execution_capacity.cumulative_cleanup import collect_settlement
from scripts.execution_capacity.final_inventory import retained_history
from scripts.execution_capacity.inventory_sql import IDENTITY, plain
from scripts.execution_capacity.original_dictionaries import key_identity
from scripts.execution_capacity.physical import replay_physical
from scripts.execution_capacity.retained_source import (
    OriginalTrace,
    RetainedInventoryQueries,
    RetainedSourceReader,
)
from scripts.execution_capacity.retained_versions import RetainedVersionWork, typed
from scripts.execution_capacity.storage_inventory import StorageInventory, replay_storage
from scripts.execution_capacity.writer_base import _complete
from scripts.execution_capacity.writer_lifecycle import writer_journal_issues
from sqlalchemy import inspect, select

from app.domain.evaluation.environment import EnvironmentLease
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.models.user import UserORM


class RetainedHistory:
    def __init__(self, records, *, budget, owner=None):
        if set(records) != {*HISTORY_FAMILIES, *PREDICATE_FAMILIES}:
            raise ValueError("original history families incomplete")
        self.values, self.budget, self.owner = records, budget, owner
        self.prefix = None
        if owner is not None:
            from scripts.execution_capacity.original_journal import OriginalJournal

            if type(owner) is not OriginalJournal or budget is not owner.budget:
                raise ValueError("actual history original owner/budget required")
            owner._usable()
            budget.reserve(256, rows=1, largest=256)
            ordinal = owner.index.append("history-sessions", b"{}")
            self.prefix = "history:" + str(ordinal) + ":"
        for kind, values in records.items():
            from scripts.execution_capacity.original_dictionaries import (
                BaseDictionaryRows,
                DictionaryRows,
                PlainBaseDictionaryRows,
                PlainDictionaryRows,
            )

            if type(values) in (
                DictionaryRows,
                PlainDictionaryRows,
                BaseDictionaryRows,
                PlainBaseDictionaryRows,
            ):
                if owner is None or values.owner is not owner:
                    raise ValueError("actual finite history owner required")
                owner._collection_metadata(values)
            elif type(values) is not dict:
                raise ValueError("original history body/receipt coverage differs")
            for key, row in values.items():
                self._row(row)
                if owner is not None:
                    if type(key) is not str:
                        raise ValueError("original history identity type differs")
                    budget.reserve(len(key) * 8 + 256, rows=1, largest=len(key) * 8 + 256)
                    owner.index.append(
                        self.prefix + kind, encode(key), keys={"key": key_identity(owner, key)}
                    )
                    if kind == "environment_read":
                        body = row["body"]
                        if type(body) is dict and body.get("reads"):
                            from scripts.execution_capacity.replay_relations import (
                                typed_relation_key,
                            )

                            identity = typed_relation_key(
                                [body["batch_id"], body["reads"][0]], owner=owner, budget=budget
                            )
                            budget.reserve(
                                len(identity.encode()) + len(encode(key)) + 256,
                                rows=1,
                                largest=max(len(identity.encode()), len(encode(key))),
                            )
                            owner.index.group(self.prefix + "lease-read", identity, encode(key))

    @staticmethod
    def _row(value):
        if type(value) is not dict or set(value) != {"body", "receipt"}:
            raise ValueError("original history body/receipt coverage differs")
        return value

    def _indexed_records(self, kind):
        self.owner._usable()
        if len(self.values[kind]) != self.owner.index.count(self.prefix + kind):
            raise ValueError("original history membership differs")
        for raw in self.owner.index.identity_rows(self.prefix + kind, "key"):
            self.owner._usable()
            self.budget.reserve(len(raw) * 4 + 128, rows=1, largest=len(raw) * 4 + 128)
            key = strict_json(raw)
            if key not in self.values[kind]:
                raise ValueError("original history identity absent")
            yield key, self._row(self.values[kind][key])
        self.owner._usable()
        if len(self.values[kind]) != self.owner.index.count(self.prefix + kind):
            raise ValueError("original history membership differs")

    def records(self, kind):
        if kind not in self.values:
            raise ValueError("original history family absent")
        if self.owner is not None:
            return self._indexed_records(kind)
        self.budget.reserve(len(self.values[kind]) * 16, rows=len(self.values[kind]))
        return sorted(self.values[kind].items())

    def get(self, kind, key):
        if kind not in self.values:
            raise ValueError("original history family absent")
        key = str(key)
        if self.owner is not None:
            self.owner._usable()
            self.budget.reserve(len(key) * 4 + 128, rows=1, largest=len(key) * 4 + 128)
            raw = self.owner.index.find(self.prefix + kind, "key", key_identity(self.owner, key))
            if (raw is None) != (key not in self.values[kind]):
                raise ValueError("original history identity membership differs")
        value = self.values[kind].get(key)
        return None if value is None else self._row(value)

    def lease_read(self, batch, first_read):
        if self.owner is None:
            candidates = (
                (key, row)
                for key, row in self.records("environment_read")
                if row["body"]["batch_id"] == batch
                and row["body"]["reads"]
                and row["body"]["reads"][0] == first_read
            )
        else:
            from scripts.execution_capacity.replay_relations import same_value, typed_relation_key

            identity = typed_relation_key([batch, first_read], owner=self.owner, budget=self.budget)

            def indexed():
                for raw in self.owner.index.group_rows(self.prefix + "lease-read", identity):
                    self.budget.reserve(len(raw) * 4 + 128, rows=1, largest=len(raw) * 4 + 128)
                    key = strict_json(raw)
                    row = self.get("environment_read", key)
                    if (
                        row is None
                        or row["body"]["batch_id"] != batch
                        or not row["body"]["reads"]
                        or not same_value(
                            row["body"]["reads"][0],
                            first_read,
                            owner=self.owner,
                            budget=self.budget,
                        )
                    ):
                        raise ValueError("original lease index candidate differs")
                    yield key, row

            candidates = indexed()
        result = None
        for candidate in candidates:
            if result is not None:
                raise ValueError("unique original lease read missing")
            result = candidate
        if result is None:
            raise ValueError("unique original lease read missing")
        return result

    def parent(self, kind, key):
        row = self.get(kind, key)
        if row is None:
            raise ValueError("original history parent absent")
        return row["body"]

    def matches(self, kind, key, body, receipt=None):
        if self.get(kind, key) != {"body": body, "receipt": receipt}:
            raise ValueError("original history semantic relationship differs")


class RetainedTransport:
    def __init__(self, rows, *, budget):
        self.rows, self.budget, self.position = rows, budget, 0

    def __call__(self, *args):
        if self.position == len(self.rows):
            raise ValueError("original transport absent")
        row = self.rows[self.position]
        self.position += 1
        if (
            set(row)
            != {
                "request",
                "start_ns",
                "end_ns",
                "stdout",
                "stderr",
                "returncode",
                "error",
                "cleanup_errors",
            }
            or list(row["request"]) != list(args)
            or row["error"] is not None
            or row["cleanup_errors"]
            or row["returncode"] != 0
            or row["stderr"] != b""
            or type(row["stdout"]) is not bytes
            or not 0 < row["start_ns"] <= row["end_ns"]
        ):
            raise ValueError("original transport request/result differs")
        self.budget.reserve(len(row["stdout"]) * 64, rows=1)
        return row["stdout"]


def replay_principals(trace, binding):
    from scripts.execution_capacity.seal_cleanup import observer_principal

    required = {binding["principal_id"], binding["probe"]["principal_id"]}
    observed = set()
    uow = None
    for _ in required:
        row = trace.take("principal-source")
        if (
            set(row) != {"read", "operation", "identity", "value"}
            or row["operation"] != "db_user_repository.py"
            or set(row["identity"]) != {"id"}
        ):
            raise ValueError("original observer principal read differs")
        identity = row["identity"]["id"]
        if identity not in required or identity in observed:
            raise ValueError("original observer principal set differs")
        observed.add(identity)
        index = trace.sql_position
        sql = trace.dispatch(select(UserORM).where(UserORM.id == identity))
        if row["read"] != {"sql_index": index, "uow": sql["uow"], "snapshot": sql["snapshot"]}:
            raise ValueError("original observer principal SQL differs")
        if uow is None:
            uow = sql["uow"]
        if sql["uow"] != uow:
            raise ValueError("original principal UOW changed")
        raw = row["value"]
        if (
            raw is None
            or set(raw) != {c.key for c in inspect(UserORM).column_attrs}
            or sql["preflight"]["row_count"] != 1
        ):
            raise ValueError("original principal model missing")
        user = UserORM(**raw).to_domain()
        trace.set_principal("user:" + identity, typed(observer_principal(user, identity)))


async def replay_lease_read(trace, journal, batch, expected):
    # The exact first InventoryQueries receipt identifies this observation;
    # historical entries are never selected by latest/name-only heuristics.
    next_row = trace.roots["operands"]["query-rows"][trace.positions["query-rows"]]
    key, record = journal.lease_read(str(batch["id"]), next_row["read"])
    body = record["body"]
    if (
        set(body)
        != {
            "batch_id",
            "scope",
            "final",
            "start_ns",
            "end_ns",
            "reads",
            "leases",
            "attempts",
            "operations",
            "error",
            "database",
            "query_observations",
        }
        or trace.plain_digest(body) != key
        or record["receipt"] is not None
        or body["final"] is not True
        or body["error"] is not None
        or body["scope"] != batch["scope_key"]
        or not 0 < body["start_ns"] <= body["end_ns"]
    ):
        raise ValueError("original final lease observation differs")
    query = RetainedInventoryQueries(trace, expected_reads=body["reads"])
    sql_start = trace.sql_position
    database = plain(await query.rows("database", IDENTITY))
    lease_operand = trace.roots["operands"]["query-rows"][trace.positions["query-rows"]]
    # final=True enumerates every row; the original clean UUID cache is retained
    # exactly but cannot filter this query's complete population.
    clean = lease_operand["parameters"]["clean"]
    if any(type(value) is not UUID for value in clean) or len(set(clean)) != len(clean):
        raise ValueError("original lease cache type differs")
    rows = await query.rows(
        "leases",
        LEASE_SQL,
        {"scope": body["scope"], "batch": str(batch["id"]), "final": True, "clean": clean},
    )
    leases = []
    attempts_seen = {}
    operations_seen = {}
    for row in rows:
        lease = EnvironmentLease.model_validate(
            {name: row[name] for name in EnvironmentLease.model_fields}
        )
        identity = str(lease.id)
        attempts = await query.rows(
            "attempts:" + identity,
            ATTEMPT_SQL,
            {
                "scope": body["scope"],
                "batch": UUID(str(batch["id"])),
                "case": lease.case_slot.case_id,
                "config": lease.case_slot.config_version,
                "repeat": lease.case_slot.repeat - 1,
            },
        )
        operations = await query.rows(
            "operations:" + identity, OPERATION_SQL, {"scope": body["scope"], "id": lease.id}
        )
        if not lease_observation_clean(lease, attempts, operations, body["scope"]):
            raise ValueError("original lease unresolved")
        lease_body = lease.model_dump(mode="json")
        journal.matches(
            "lease_state",
            f"{identity}:{lease.revision}",
            {"lease_id": identity, "revision": lease.revision, "state": lease.state},
        )
        journal.matches(
            "lease",
            identity,
            {
                "scope": body["scope"],
                "batch_id": str(batch["id"]),
                "namespace": lease.namespace,
                "environment_version": str(lease.environment_version),
                "generation": lease.generation,
                "case_slot": lease_body["case_slot"],
            },
            {"state": "verified_clean", "namespace": lease.namespace},
        )
        for operation in operations:
            raw = plain(operation)
            oid = (
                str(operation["id"])
                + ":"
                + sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
            )
            journal.matches("environment_observation", oid, raw)
        leases.append(lease_body)
        attempts_seen[identity] = plain(attempts)
        operations_seen[identity] = plain(operations)
    from scripts.execution_capacity.predicate_history import link_current_observations

    link_current_observations(
        body["query_observations"], trace.roots["sql"], sql_start, trace.sql_position
    )
    query.finish_outputs()
    if (
        not leases
        or leases != expected
        or not trace.plain_equal(body["database"], database)
        or not trace.plain_equal(body["leases"], rows)
        or not trace.plain_equal(body["attempts"], attempts_seen)
        or not trace.plain_equal(body["operations"], operations_seen)
        or not trace.plain_equal(body["reads"], query.reads)
        or not all(
            body["start_ns"] <= r["start_ns"] <= r["end_ns"] <= body["end_ns"] for r in query.reads
        )
    ):
        raise ValueError("original lease complete read differs")


async def replay_final(
    roots, *, origin, base, budget, signing_secret, cursor_secret, owner=None, base_owner=None
):
    trace = OriginalTrace(roots, budget=budget, owner=owner)
    bundles = roots["operands"]["final-input"]
    if len(bundles) != 1:
        raise ValueError("exact original final bundle required")
    bundle = bundles[0]
    identity = bundle["identity"]
    if (
        set(identity) != {"binding", "bucket", "origin", "base"}
        or SourceOrigin.model_validate(identity["origin"]) != origin
    ):
        raise ValueError("original final identity differs")
    expected_base = [] if base is None else [base["source_inventory"]]
    from scripts.execution_capacity.evidence_json import chunks, equal_streams

    if not equal_streams(
        chunks(typed(identity["base"]), budget=budget, owner=owner),
        chunks(typed(expected_base), budget=budget, owner=base_owner),
    ):
        raise ValueError("original full inherited base differs")
    replay_principals(trace, identity["binding"])
    trace.begin(bundle, "final-input")
    cleanup = roots["cleanup"]
    quiescence = cleanup["quiescence"] if origin.kind == "base" else cleanup
    final = quiescence["final"]
    from scripts.execution_capacity.cumulative_cleanup import validate_quiescence

    validate_quiescence(quiescence)
    if (
        quiescence["complete"] is not True
        or quiescence["errors"]
        or final["complete"] is not True
        or final["issues"]
        or not 0
        < quiescence["started_ns"]
        <= final["start_ns"]
        <= final["end_ns"]
        <= quiescence["ended_ns"]
    ):
        raise ValueError("original final quiescence incomplete")
    inherited = (
        None
        if base is None
        else SimpleNamespace(
            **{name: base["writer_base"][name] for name in ("writers", "uploads", "supervisors")}
        )
    )
    writers = final["writers"]
    issues = writer_journal_issues(
        writers["writers"],
        writers["supervisors"],
        writers["uploads"],
        writers["writer_ids"],
        inherited,
    )
    if issues or writers["issues"] != issues:
        raise ValueError("original writer journal unresolved")
    _complete(
        {
            "errors": quiescence["errors"],
            "writers": writers["writers"],
            "supervisors": writers["supervisors"],
            "uploads": writers["uploads"],
            "exits": quiescence["writers"],
        }
    )
    # Actual before/after exit predicate is shared with ContainerWriters._stop.
    from scripts.execution_capacity.writer_lifecycle import replay_writer_exits

    replay_writer_exits(writers, quiescence["writers"], budget=budget)
    source = await RetainedSourceReader(
        trace, signing_secret=signing_secret, cursor_secret=cursor_secret
    ).replay(final["source"], origin=origin, base=() if base is None else base["source_cohorts"])
    source_bundle = roots["operands"]["source-input"][0]
    if source_bundle["identity"]["binding"] != identity["binding"]:
        raise ValueError("original final/source deployment differs")
    settlement = {"rows": {}, "reads": [], "issues": [], "complete": False}
    query = RetainedInventoryQueries(trace, expected_reads=final["settlement"]["reads"])
    await collect_settlement(query, source, settlement)
    settlement["counts"] = {key: len(rows) for key, rows in settlement["rows"].items()}
    if not trace.plain_equal(settlement, final["settlement"]) or settlement["issues"]:
        raise ValueError("original settlement differs")
    records = {**final["retained_history"]["records"], **final["predicate_journals"]}
    journal = RetainedHistory(records, budget=budget, owner=trace.owner)
    history = retained_history(
        journal,
        writers["uploads"],
        settlement["rows"],
        source,
        expected_issues=final["retained_history"]["issues"],
    )
    if not trace.plain_equal(history, final["retained_history"]) or history["issues"]:
        raise ValueError("original retained history differs")
    batches = settlement["rows"]["evaluation_batches"]
    if {key for key in final if key.startswith("leases:")} != {
        "leases:" + row["id"] for row in batches
    }:
        raise ValueError("original final lease coverage differs")
    for batch in batches:
        await replay_lease_read(trace, journal, batch, final["leases:" + batch["id"]])
    storage_raw = final["storage"]
    if set(storage_raw) != {field.name for field in fields(StorageInventory)}:
        raise ValueError("original storage fields differ")
    storage = replay_storage(StorageInventory(**storage_raw), identity["bucket"], budget=budget)
    expected = {}
    for row in source.objects:
        value = {"size": row["size_bytes"], "sha256": row["sha256"]}
        if row["key"] in expected and expected[row["key"]] != value:
            raise ValueError("original object identity conflicts")
        expected[row["key"]] = value
    storage.require_quiescent(expected)
    transport = RetainedTransport(roots["transports"], budget=budget)
    broker = read_broker(identity["binding"], transport, budget=budget)
    if broker.__dict__ != final["broker"] or broker.errors or not broker.complete:
        raise ValueError("original broker differs")
    originals = request_parents(expected_requests(journal), journal)
    if reconcile(broker, originals):
        raise ValueError("original broker request relation differs")
    versions = {}
    for record in originals.operations.values():
        request = record["request"]
        scope = request["lease"]["case_slot"]["workspace"]
        vid = request["lease"]["environment_version"]
        if (scope, vid) not in versions:
            pinned = trace.take("pinned-input")
            trace.begin(pinned, "pinned-input")
            raw = pinned["identity"]
            owner_scope = OwnerScope.model_validate(raw["scope"])
            principal = Principal.model_validate(raw["principal"])
            if (
                "user:" + owner_scope.user_id != scope
                or raw["id"] != UUID(vid)
                or typed(principal) != trace.principal(scope)
            ):
                raise ValueError("original pinned version identity differs")
            work = RetainedVersionWork.from_bundle(
                pinned,
                roots["operands"],
                roots["sql"],
                roots["objects"],
                cursor_secret=cursor_secret,
                budget=budget,
                family="pinned-input",
            )
            version = await work.services(owner_scope, principal).environments.version(
                owner_scope, principal, UUID(vid)
            )
            work.finish()
            trace.consumed(pinned)
            versions[(scope, vid)] = version.model_dump(mode="json")
        if versions[(scope, vid)] != request["version"]:
            raise ValueError("original full pinned version differs")
    if final["pinned_environment_versions"] != [
        {"scope": key[0], "id": key[1], "version": value} for key, value in sorted(versions.items())
    ]:
        raise ValueError("original pinned version coverage differs")
    for row in final["physical_observations"]:
        before = transport.position
        raw = transport(*row["request"])
        observed = transport.rows[before]
        if (
            raw.decode("utf-8", errors="strict") != row["response"]
            or not row["start_ns"] <= observed["start_ns"] <= observed["end_ns"] <= row["end_ns"]
        ):
            raise ValueError("original physical transport differs")
    if replay_physical(journal, broker, final["physical_observations"]) != final[
        "physical"
    ] or transport.position != len(transport.rows):
        raise ValueError("original physical readback differs")
    from scripts.execution_capacity.predicate_history import validate_predicate_history

    validate_predicate_history(
        final,
        budget=budget,
        sql=roots["sql"],
        base=None if base is None else base["final"],
        base_owner=base_owner,
    )
    counts = {
        "database": settlement["counts"],
        "storage": {
            "objects": len(storage.objects),
            "uploads": len(storage.uploads),
            "pages": len(storage.pages),
        },
        "broker": {
            "operations": len(broker.operations),
            "bindings": len(broker.bindings),
            "pages": len(broker.pages),
        },
    }
    if counts != final["counts"]:
        raise ValueError("original final counts differ")
    trace.end(bundle)
    trace.take("final-input")
    if (
        trace.sql_position != len(roots["sql"])
        or trace.object_position != len(roots["objects"])
        or any(trace.positions[name] != len(rows) for name, rows in roots["operands"].items())
    ):
        raise ValueError("unconsumed complete original evidence")
    from scripts.execution_capacity.pg_diagnostics_timed import (
        diagnostic_inventory,
        replay_original,
    )

    for diagnostic in quiescence["diagnostics"]:
        original_inventory = diagnostic_inventory(
            diagnostic,
            source,
            None if base is None else base.get("diagnostic_inventory"),
            budget=budget,
        )
        replay_original(diagnostic, original_inventory, budget=budget)
    return source
