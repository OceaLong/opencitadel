"""Named full enumeration queries and same-snapshot read identity for C2c."""

import json
import time
from contextlib import asynccontextmanager
from hashlib import sha256
from uuid import UUID, uuid4

from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
from sqlalchemy import text

from app.infrastructure.security.db_authorization import configure_session_authorization


def plain(value):
    from scripts.execution_capacity.original_collections import (
        BaseCollectionRows,
        CollectionRows,
        PlainBaseCollectionRows,
        PlainCollectionRows,
    )
    from scripts.execution_capacity.original_dictionaries import (
        BaseDictionaryRows,
        DictionaryRows,
        PlainBaseDictionaryRows,
        PlainDictionaryRows,
    )

    if type(value) in (
        DictionaryRows,
        PlainDictionaryRows,
        BaseDictionaryRows,
        PlainBaseDictionaryRows,
    ):
        value.owner._collection_metadata(value)
        kind = (
            PlainBaseDictionaryRows
            if type(value) in (BaseDictionaryRows, PlainBaseDictionaryRows)
            else PlainDictionaryRows
        )
        return kind(value.owner, value.producer_ordinal, value.length, value.body_descriptor)
    if type(value) in (
        CollectionRows,
        PlainCollectionRows,
        BaseCollectionRows,
        PlainBaseCollectionRows,
    ):
        value.owner._collection_metadata(value)
        kind = (
            PlainBaseCollectionRows
            if type(value) in (BaseCollectionRows, PlainBaseCollectionRows)
            else PlainCollectionRows
        )
        return kind(value.owner, value.producer_ordinal, value.length, value.body_descriptor)
    from collections.abc import Sequence

    def scalar(item):
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            raise TypeError("explicit original collection conversion required")
        return str(item)

    return json.loads(json.dumps(value, default=scalar, sort_keys=True))


def collection_digest(rows, *, journal):
    """Exact legacy JSON result commitment from finite actual original rows."""
    if journal is None:
        return canonical_digest(plain(rows))
    journal._collection_metadata(rows)
    hashed = sha256(b"[")
    size = len(rows)
    for ordinal in range(size):
        if ordinal:
            hashed.update(b",")
        # Each row is independently bounded and all its original types remain
        # retained. This public read digest uses the existing plain JSON rule.
        raw = json.dumps(
            plain(rows[ordinal]), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        journal.budget.reserve(len(raw), rows=0, largest=len(raw))
        hashed.update(raw)
    if len(rows) != size:
        raise ValueError("original query collection changed")
    hashed.update(b"]")
    return hashed.hexdigest()


def validate_preflight(preflight, budget):
    sizes = [preflight[k] for k in ("row_count", "max_bytes", "total_bytes")]
    if (
        any(type(n) is not int or n < 0 for n in sizes)
        or preflight["read_only"] != "on"
        or preflight["isolation"] != "repeatable read"
        or not isinstance(preflight["snapshot"], str)
        or not preflight["snapshot"]
        or (sizes[0] == 0) != (sizes[1] == sizes[2] == 0)
        or (sizes[0] > 0 and not 0 < sizes[1] <= sizes[2] <= sizes[0] * sizes[1])
    ):
        raise ValueError("invalid bounded query preflight")
    if (
        sizes[0] + budget.rows > budget.rows_limit
        or sizes[1] > budget.row_limit
        or sizes[2] + budget.bytes > budget.bytes_limit
    ):
        raise EvidenceQuotaError("private query input quota exceeded")
    budget.check(sizes[2], rows=sizes[0], largest=sizes[1])


class InventoryQueries:
    def __init__(self, db, *, budget=None, snapshot_owned=False):
        self.db, self.reads = db, []
        self.originals = []
        self.read_id = str(uuid4())
        self._inflight = self._outputs_finished = self._outputs_aborted = False
        self.snapshot_owned = snapshot_owned
        from scripts.execution_capacity.observer_session import ObserverSession

        sync = getattr(db, "sync_session", None)
        self.observer = sync if isinstance(sync, ObserverSession) else None
        self.original_owner = None if self.observer is None else self.observer.evidence.journal
        self.budget = (
            budget
            if budget is not None
            else (
                self.observer.evidence_budget.child()
                if self.observer is not None
                else EvidenceBudget()
            )
        )

        if self.original_owner is not None:
            from scripts.execution_capacity.query_outputs import query_output

            parent = self.observer.evidence._cleanup_token
            self.reads = query_output(
                self.original_owner, parent=parent, slot="query-reads:" + self.read_id
            )
            self.originals = query_output(
                self.original_owner, parent=parent, slot="query-originals:" + self.read_id
            )

    def finish_outputs(self):
        if self._inflight or self._outputs_aborted:
            raise ValueError("query output still in flight or aborted")
        if not self._outputs_finished:
            if self.original_owner is not None:
                try:
                    self.reads = self.reads.complete()
                    self.originals = self.originals.complete()
                except BaseException:
                    self._outputs_aborted = True
                    raise
            self._outputs_finished = True
        return self.reads

    async def rows(self, name, sql, params=None):
        if self._inflight or self._outputs_finished or self._outputs_aborted:
            raise ValueError("query output is not accepting queries")
        params = params or {}
        record = {
            "name": name,
            "sql_digest": canonical_digest(sql),
            "parameter_digest": canonical_digest(plain(params)),
            "parameters": plain(params),
            "start_ns": time.monotonic_ns(),
        }
        journal = self.observer.evidence.journal if self.observer is not None else None
        token = writer = dispatch = None
        receipt_ready = False
        self._inflight = True
        try:
            if journal is not None:
                token = journal.begin(
                    "operand:query-rows",
                    {
                        "name": name,
                        "statement": sql,
                        "parameters": params,
                        "read_id": self.read_id,
                        "ordinal": len(self.originals),
                        "read": record,
                    },
                )
                writer = journal.begin_collection(token, "rows")
            if self.observer is None:
                preflight = dict(
                    (
                        await self.db.execute(
                            text(
                                "SELECT count(*) AS row_count, coalesce(max(octet_length(row_to_json(q)::text)),0) AS max_bytes, "
                                "coalesce(sum(octet_length(row_to_json(q)::text)),0)::bigint AS total_bytes, "
                                "current_setting('transaction_read_only') AS read_only, "
                                "current_setting('transaction_isolation') AS isolation, txid_current_snapshot()::text AS snapshot "
                                "FROM (" + sql + ") AS q"
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .one()
                )
                record["preflight"] = preflight
                validate_preflight(preflight, self.budget)
                result = await self.db.stream(text(sql), params)
            else:
                before_dispatch = self.observer.evidence_dispatch_count
                result = await self.db.stream(text(sql), params)
                dispatch = self.observer.latest_sql(after=before_dispatch)
                preflight = dispatch[1]["preflight"]
                record["preflight"] = preflight
            rows = [] if writer is None else writer
            try:
                async for value in result.mappings():
                    row = dict(value)
                    self.budget.charge(row)
                    rows.append(row)
            finally:
                await result.close()
            if writer is not None:
                rows = writer.complete()
            if len(rows) != preflight["row_count"]:
                raise ValueError("query snapshot cardinality changed")
            record.update(rows=len(rows), result_digest=collection_digest(rows, journal=journal))
            record["end_ns"] = time.monotonic_ns()
            original, indexes = None, []
            if self.observer is not None:
                owner = self.observer.evidence
                if self.observer.evidence_dispatch_count != before_dispatch + 1:
                    raise ValueError("actual query dispatch original changed during result")
                dispatch_token, original = dispatch
                indexes = [owner.sql_ordinal(dispatch_token, original)]
                retained = {
                    "name": name,
                    "rows": rows,
                    "statement": sql,
                    "parameters": params,
                    "sql_index": indexes[0],
                    "read": record,
                }
                if journal is not None:
                    journal.complete(token, retained)
                else:
                    owner.retain("query-rows", retained)
            from scripts.execution_capacity.evidence_owner import copy_original

            self.originals.append(
                copy_original(
                    {
                        "read_id": self.read_id,
                        "ordinal": len(self.originals),
                        "owner": "observer"
                        if self.observer is not None
                        else "readonly-snapshot"
                        if self.snapshot_owned
                        else "unowned",
                        "name": name,
                        "statement": sql,
                        "parameters": params,
                        "parameter_types": parameter_types(params),
                        "rows": rows,
                        "read": record,
                        "sql_index": indexes[0] if indexes else None,
                        "dispatch": original,
                    },
                    budget=self.budget,
                    owner=journal,
                )
            )
            receipt_ready = True
            return rows
        except Exception as error:
            record["error"] = type(error).__name__
            record.setdefault("end_ns", time.monotonic_ns())
            if (
                journal is not None
                and token is not None
                and journal.valid
                and journal.index.find("end:" + token.family, "ordinal", str(token.ordinal)) is None
            ):
                journal.complete(
                    token,
                    {
                        "name": name,
                        "statement": sql,
                        "parameters": params,
                        "read": record,
                        "collection_producer": None if writer is None else writer.token.ordinal,
                    },
                )
            receipt_ready = True
            raise
        finally:
            record.setdefault("end_ns", time.monotonic_ns())
            self._inflight = False
            if receipt_ready:
                try:
                    self.reads.append(record)
                except BaseException:
                    self._outputs_aborted = True
                    raise
            else:
                self._outputs_aborted = True


@asynccontextmanager
async def read_snapshot(sessions, authorization, *, budget=None):
    async with sessions() as db:
        await db.rollback()
        await db.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
        await configure_session_authorization(db, authorization)
        query = None
        failures = []
        try:
            query = InventoryQueries(db, snapshot_owned=True, budget=budget)
            yield query
        except BaseException as error:  # noqa: BLE001 - preserve original cancellation and all close failures
            failures.append(error)
        finally:
            try:
                if query is not None:
                    query.finish_outputs()
            except BaseException as error:  # noqa: BLE001 - grouped with acquisition and rollback below
                failures.append(error)
            try:
                await db.rollback()
            except BaseException as error:  # noqa: BLE001 - retain rollback without losing primary failure
                failures.append(error)
            if len(failures) == 1:
                raise failures[0]
            if failures:
                raise BaseExceptionGroup(
                    "snapshot acquisition, outputs or rollback failed", failures
                )


IDENTITY = """SELECT current_database() AS database_name,current_user AS database_user,
 (SELECT system_identifier::text FROM pg_control_system()) AS database_system_identifier,
 txid_current_snapshot()::text AS snapshot,
 current_setting('transaction_read_only') AS read_only,
 current_setting('transaction_isolation') AS isolation,
 current_setting('server_version_num') AS server_version,
 (SELECT array_agg(version_num ORDER BY version_num) FROM alembic_version) AS migrations"""
_OWNERS_RELATION = """SELECT s.stream_type,s.stream_id,s.owner_scope_key,p.source_entity_type,p.source_entity_id,p.correlation_id,
 p.stream_version,p.terminal FROM execution_stream_owners s LEFT JOIN execution_run_projection p
 ON s.stream_type='run' AND p.run_id::text=s.stream_id"""
_OWNERS_ORDER = " ORDER BY s.stream_type,s.stream_id"
OWNERS = _OWNERS_RELATION + _OWNERS_ORDER
# Fixed physical page candidates only. The production OWNERS read still uses
# the unpaged logical query above; no page protocol is dispatched here.
OWNERS_PAGE_FIRST = _OWNERS_RELATION + _OWNERS_ORDER + " LIMIT :page_size"
OWNERS_PAGE_AFTER = (
    _OWNERS_RELATION
    + " WHERE (s.stream_type,s.stream_id) > (:after_type,:after_id)"
    + _OWNERS_ORDER
    + " LIMIT :page_size"
)
ATTEMPTS = """SELECT a.*,r.batch_id,r.case_revision_id,r.config_version_id,r.repetition,r.attempt AS current_attempt FROM evaluation_batch_attempts a
 JOIN evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id
 ORDER BY a.scope_key,r.batch_id,a.result_id,a.attempt"""
JUDGES = "SELECT * FROM evaluation_judge_intents ORDER BY scope_key,batch_id,id"
PROJECTORS = """SELECT h.owner_scope_key AS scope,h.head_position AS head,c.last_position AS checkpoint,
 COALESCE(v.active_generation::text,'live') AS generation,
 CASE WHEN v.active_generation IS NULL THEN 1 ELSE g.source_version END AS source_version,
 CASE WHEN v.active_generation IS NULL THEN 1 ELSE g.algorithm_version END AS algorithm_version
 FROM execution_scope_head h LEFT JOIN execution_projector_checkpoints c
 ON c.owner_scope_key=h.owner_scope_key AND c.projector_name='formal'
 LEFT JOIN execution_view_controls v ON v.scope_key=h.owner_scope_key
 LEFT JOIN execution_view_generations g ON g.scope_key=v.scope_key AND g.generation=v.active_generation
 ORDER BY h.owner_scope_key"""


def parameter_types(value):
    """Closed original parameter type tree; journals do not decode arbitrary types."""
    if isinstance(value, dict):
        return {key: parameter_types(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [parameter_types(item) for item in value]
    if type(value) in (str, int, float, bool, UUID) or value is None:
        return "null" if value is None else type(value).__name__
    raise ValueError("unsupported original query parameter type")
