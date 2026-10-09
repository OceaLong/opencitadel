"""Private write-ahead recovery inventory and transparent production observers.

SQLite is local recovery storage, never a source of execution facts. Every intent
commits with FULL synchronization before its external write. Envelopes, object
keys and configuration bodies stay inside the operator's private directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from contextvars import ContextVar
from hashlib import sha256
from uuid import uuid4

from scripts.execution_capacity.ownership import _open_private, _private_directory

from app.domain.external.object_storage import ObjectNotFoundError

PORT_UPLOAD = ContextVar("capacity_port_upload", default=None)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RecoveryJournal:
    def __init__(self, root, *, budget=None, index_bytes=None):
        self.budget, self.index_bytes = budget, index_bytes
        self._native_views = []
        self._read_scope = None
        self._closed = False
        _private_directory(root)
        self.root = root
        path = root / "recovery.sqlite3"
        fd = _open_private(path, os.O_RDWR | os.O_CREAT)
        os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS intents (kind TEXT, identity TEXT, body TEXT NOT NULL, receipt TEXT, PRIMARY KEY(kind,identity))"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS intent_activity ON intents(kind,json_extract(body, '$.activity_id'))"
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(intents)")}
        if "body_version" not in columns:
            self.db.execute(
                "ALTER TABLE intents ADD COLUMN body_version INTEGER NOT NULL DEFAULT 1"
            )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS original_bodies (kind TEXT, identity TEXT, version INTEGER NOT NULL, reference TEXT NOT NULL, PRIMARY KEY(kind,identity))"
        )
        self.db.commit()
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._read_scope is not None:
            self._read_scope.close()
        self._closed = True
        for view in getattr(self, "_native_views", ()):
            view.close()
        self.db.close()

    def read_scope(self, *, budget=None):
        return _RecoveryReadScope(self, self.budget if budget is None else budget)

    def intent(self, kind, identity, body, *, body_owner=None):
        if body_owner is not None:
            self._original_intent(kind, str(identity), body, body_owner)
            return
        if self.db.execute(
            "SELECT 1 FROM original_bodies WHERE kind=? AND identity=?", (kind, str(identity))
        ).fetchone():
            raise ValueError("explicit native original owner required for immutable intent")
        body = encoded(body)
        old = self.db.execute(
            "SELECT body FROM intents WHERE kind=? AND identity=?", (kind, str(identity))
        ).fetchone()
        if old:
            if old[0] != body:
                raise ValueError("immutable recovery intent differs")
            return
        with self.db:
            self.db.execute(
                "INSERT INTO intents(kind,identity,body) VALUES(?,?,?)", (kind, str(identity), body)
            )

    def _original_intent(self, kind, identity, body, body_owner):
        from scripts.execution_capacity.evidence_json import equal_streams
        from scripts.execution_capacity.native_original_bodies import write_body
        from scripts.execution_capacity.original_plain import (
            canonical_original_digest,
            canonical_parts,
        )

        if kind != "environment_read" or self.budget is None or self.index_bytes is None:
            raise ValueError("explicit bounded environment read original configuration required")
        if canonical_original_digest(body, owner=body_owner, budget=self.budget) != identity:
            raise ValueError("native environment read identity differs")
        with self.read_scope() as scope:
            old = scope.get(kind, identity)
            if old is not None:
                prior = scope.native_view(old["body"])
                if not equal_streams(
                    canonical_parts(
                        old["body"],
                        owner=None if prior is None else prior.journal,
                        budget=self.budget,
                    ),
                    canonical_parts(body, owner=body_owner, budget=self.budget),
                ):
                    raise ValueError("immutable recovery intent differs")
                return
        reference = write_body(
            self.root,
            identity,
            body,
            source=body_owner,
            budget=self.budget,
            index_bytes=self.index_bytes,
        )
        with self.db:
            self.db.execute(
                "INSERT INTO intents(kind,identity,body,body_version) VALUES(?,?,?,?)",
                (kind, identity, "null", 2),
            )
            self.db.execute(
                "INSERT INTO original_bodies(kind,identity,version,reference) VALUES(?,?,?,?)",
                (kind, identity, 2, encoded(reference)),
            )

    def _body_value(self, kind, identity, body, *, budget=None, scope=None):
        from scripts.execution_capacity.native_original_bodies import NativeBodyView

        columns = {row[1] for row in self.db.execute("PRAGMA table_info(intents)")}
        version = (
            self.db.execute(
                "SELECT body_version FROM intents WHERE kind=? AND identity=?", (kind, identity)
            ).fetchone()[0]
            if "body_version" in columns
            else 1
        )
        formats = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='original_bodies'"
        ).fetchone()
        reference = (
            None
            if formats is None
            else self.db.execute(
                "SELECT version,typeof(reference),length(CAST(reference AS BLOB)) FROM original_bodies WHERE kind=? AND identity=?",
                (kind, identity),
            ).fetchone()
        )
        if type(version) is not int or version not in (1, 2):
            raise ValueError("closed native body version required")
        if version == 1:
            if reference is not None:
                raise ValueError("foreign native reference for legacy body")
            return json.loads(body)
        if reference is None:
            raise ValueError("missing native original reference")
        budget = self.budget if budget is None else budget
        if (
            kind != "environment_read"
            or body != "null"
            or type(reference[0]) is not int
            or reference[0] != 2
            or budget is None
            or getattr(self, "index_bytes", None) is None
        ):
            raise ValueError("complete bounded native original configuration required")
        from scripts.acceptance.capacity_io import strict_json

        if reference[1] != "text" or type(reference[2]) is not int or reference[2] < 1:
            raise ValueError("invalid native original reference storage")
        budget.reserve(reference[2] * 64, rows=1, largest=reference[2])
        reference = self.db.execute(
            "SELECT version,reference FROM original_bodies WHERE kind=? AND identity=?",
            (kind, identity),
        ).fetchone()
        budget.charge_bytes(len(reference[1].encode()))
        budget.reserve(256, rows=1, largest=256)
        if scope is None and len(self._native_views) >= budget.row_limit // 256:
            from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

            raise EvidenceQuotaError("legacy native view live population exceeds bound")
        view = NativeBodyView(
            self.root,
            identity,
            strict_json(reference[1]),
            budget=budget,
            index_bytes=self.index_bytes,
        )
        if scope is None:
            self._native_views.append(view)
        else:
            scope._usable()
            scope.view = view
        return view.body

    def native_view(self, body):
        from scripts.execution_capacity.native_original_bodies import NativeBodyView

        for view in getattr(self, "_native_views", ()):
            if type(view) is NativeBodyView and view.body is body:
                view.journal._usable()
                return view
        return None

    def original_files(self):
        """Verify all member closures, then stream their exact indexed file names."""
        from scripts.acceptance.capacity_index import CapacityIndex

        present = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='original_bodies'"
        ).fetchone()
        count = (
            0
            if not present
            else self.db.execute("SELECT count(*) FROM original_bodies").fetchone()[0]
        )
        if not count:
            with os.scandir(self.root) as entries:
                if any(entry.name.startswith("original-") for entry in entries):
                    raise ValueError("native original directory closure differs")
            return
        if self.budget is None or self.index_bytes is None:
            raise ValueError("bounded native original inventory required")
        self.budget.reserve(self.index_bytes, rows=0)
        with (
            CapacityIndex(quota_bytes=self.index_bytes, row_bytes=self.budget.row_limit) as index,
            self.read_scope() as scope,
        ):
            for kind, identity in self.db.execute(
                "SELECT kind,identity FROM original_bodies ORDER BY kind,identity"
            ):
                value = scope.get(kind, identity)
                view = None if value is None else scope.native_view(value["body"])
                if view is None:
                    raise ValueError("native original reference closure differs")
                journal, name = view.journal, view.journal.root.name
                if index.find("directories", "identity", name) is not None:
                    raise ValueError("duplicate native original owner closure")
                self.budget.reserve(len(name) + 256, rows=1, largest=len(name))
                index.append("directories", name.encode(), keys={"identity": name})
                self.budget.reserve(len(name) + 256, rows=1)
                index.append("files", (name + "/manifest.json").encode())
                for segments in (journal.body_segments, journal.log_segments):
                    for chunk in segments.chunks:
                        member = name + "/" + segments.name(chunk["ordinal"])
                        self.budget.reserve(len(member) + 256, rows=1, largest=len(member))
                        index.append("files", member.encode())
            observed = 0
            with os.scandir(self.root) as entries:
                for entry in entries:
                    if entry.name.startswith("original-"):
                        if index.find("directories", "identity", entry.name) != entry.name.encode():
                            raise ValueError("native original directory closure differs")
                        observed += 1
            if observed != count or observed != index.count("directories"):
                raise ValueError("native original directory closure differs")
            scope.close()
            for raw in index.rows("files"):
                yield raw.decode()

    def acknowledge(self, kind, identity, receipt):
        if self.budget is None:
            old = self.get(kind, identity)
        else:
            with self.read_scope() as scope:
                old = scope.get(kind, identity)
        if old is None:
            raise ValueError("acknowledgement without intent")
        receipt = encoded(receipt)
        if old["receipt"] is not None and encoded(old["receipt"]) != receipt:
            raise ValueError("immutable recovery receipt differs")
        with self.db:
            self.db.execute(
                "UPDATE intents SET receipt=? WHERE kind=? AND identity=?",
                (receipt, kind, str(identity)),
            )

    def get(self, kind, identity):
        if self.budget is not None:
            return self.bounded_get(kind, identity, self.budget)
        row = self.db.execute(
            "SELECT body,receipt FROM intents WHERE kind=? AND identity=?", (kind, str(identity))
        ).fetchone()
        return (
            None
            if row is None
            else {
                "body": self._body_value(kind, str(identity), row[0]),
                "receipt": json.loads(row[1]) if row[1] else None,
            }
        )

    def records(self, kind):
        if self.budget is not None:
            yield from self.bounded_records(kind, self.budget)
            return
        for identity, body, receipt in self.db.execute(
            "SELECT identity,body,receipt FROM intents WHERE kind=? ORDER BY identity", (kind,)
        ):
            yield (
                identity,
                {
                    "body": self._body_value(kind, identity, body),
                    "receipt": json.loads(receipt) if receipt else None,
                },
            )

    def bounded_get(self, kind, identity, budget):
        rows = list(self._bounded_rows("kind=? AND identity=?", (kind, str(identity)), budget))
        if len(rows) > 1:
            raise ValueError("duplicate private journal identity")
        return None if not rows else rows[0][1]

    def bounded_records(self, kind, budget):
        yield from self._bounded_rows("kind=?", (kind,), budget)

    def _bounded_rows(self, predicate, parameters, budget, *, scope=None):
        """Owner-thread C2c snapshot only; byte preflight precedes JSON decoding.

        A savepoint keeps the aggregate and original typed reads on one SQLite
        snapshot, also when the caller already owns a transaction. No DDL or
        resource mutation. The ordinary journal consumers remain unchanged.
        """
        from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

        if scope is None and self._read_scope is not None:
            raise ValueError("legacy read forbidden while recovery scope active")
        size_sql = (
            "coalesce(length(CAST(identity AS BLOB)),0) + "
            "coalesce(length(CAST(body AS BLOB)),0) + "
            "coalesce(length(CAST(receipt AS BLOB)),0)"
        )
        invalid_sql = (
            "CASE WHEN typeof(identity)<>'text' OR length(CAST(identity AS BLOB))=0 "
            "OR instr(identity,char(0))>0 OR typeof(body)<>'text' "
            "OR length(CAST(body AS BLOB))=0 OR typeof(receipt) NOT IN ('null','text') "
            "OR (receipt IS NOT NULL AND length(CAST(receipt AS BLOB))=0) THEN 1 ELSE 0 END"
        )
        self.db.execute("SAVEPOINT c2c_retained_read")
        try:
            count, largest, total, invalid = self.db.execute(
                "SELECT count(*),coalesce(max(" + size_sql + "),0),"
                "coalesce(sum(" + size_sql + "),0),coalesce(sum(" + invalid_sql + "),0) "
                "FROM intents WHERE " + predicate,
                parameters,
            ).fetchone()
            if (
                any(
                    type(value) is not int or value < 0
                    for value in (count, largest, total, invalid)
                )
                or invalid != 0
                or (count == 0) != (largest == total == 0)
                or (count > 0 and not 0 < largest <= total <= count * largest)
            ):
                raise ValueError("invalid private journal storage population")
            if (
                count + budget.rows > budget.rows_limit
                or largest > budget.row_limit
                or total + budget.bytes > budget.bytes_limit
            ):
                raise EvidenceQuotaError("private journal snapshot quota exceeded")
            budget.check(total, count, largest=largest)
            budget.reserve(total * 64, rows=0)
            cursor = self.db.execute(
                "SELECT identity,body,receipt FROM intents WHERE "
                + predicate
                + " ORDER BY identity",
                parameters,
            )
            observed = 0
            for identity, body, receipt in cursor:
                if scope is not None:
                    scope._usable()
                    scope._close_view()
                budget.charge_bytes(
                    sum(
                        len(value.encode("utf-8"))
                        for value in (identity, body, receipt)
                        if value is not None
                    )
                )
                observed += 1
                yield (
                    identity,
                    {
                        "body": self._body_value(
                            parameters[0], identity, body, budget=budget, scope=scope
                        ),
                        "receipt": None if receipt is None else json.loads(receipt),
                    },
                )
            if observed != count:
                raise ValueError("private journal snapshot cardinality changed")
        finally:
            self.db.execute("RELEASE c2c_retained_read")

    def activity_children(self, kind, activity_id):
        rows = self.db.execute(
            "SELECT identity,body,receipt FROM intents WHERE kind=? AND json_extract(body, '$.activity_id')=?",
            (kind, str(activity_id)),
        )
        for identity, body, receipt in rows:
            yield (
                identity,
                {"body": json.loads(body), "receipt": json.loads(receipt) if receipt else None},
            )

    def parent(self, kind, identity):
        record = self.get(kind, identity)
        if record is None:
            raise ValueError("unknown exact parent")
        return record["body"]


class _RecoveryReadScope:
    """One live native row, explicitly invalidated before advance or scope exit."""

    def __init__(self, journal, budget):
        from scripts.execution_capacity.evidence_bounds import EvidenceBudget

        if (
            type(journal) not in (RecoveryJournal, ReadOnlyRecoveryJournal)
            or journal._closed
            or journal._read_scope is not None
        ):
            raise ValueError("one active read scope per live recovery journal required")
        if type(budget) is not EvidenceBudget:
            raise ValueError("bounded recovery read scope required")
        # The slot and each opened OriginalJournal index are charged cumulatively;
        # reopening cannot replenish the authorized budget.
        budget.reserve(512, rows=1, largest=512)
        self.journal, self.budget = journal, budget
        self.view = self.iterator = None
        self.closed = False
        journal._read_scope = self

    def _usable(self):
        if self.closed or self.journal._closed or self.journal._read_scope is not self:
            raise ValueError("closed or foreign recovery read scope")

    def __enter__(self):
        self._usable()
        return self

    def __exit__(self, *_):
        self.close()

    def _close_view(self):
        if self.view is not None:
            self.view.close()
            self.view = None

    def _reset(self):
        iterator, self.iterator = self.iterator, None
        try:
            if iterator is not None:
                iterator.close()
        finally:
            self._close_view()

    def close(self):
        if not self.closed:
            try:
                self._reset()
            finally:
                self.closed = True
                if self.journal._read_scope is self:
                    self.journal._read_scope = None

    def native_view(self, body):
        self._usable()
        if self.view is None or self.view.body is not body:
            return None
        self.view.journal._usable()
        return self.view

    def get(self, kind, identity):
        self._usable()
        self._reset()
        try:
            rows = list(
                self.journal._bounded_rows(
                    "kind=? AND identity=?", (kind, str(identity)), self.budget, scope=self
                )
            )
            if len(rows) > 1:
                raise ValueError("duplicate private journal identity")
            return None if not rows else rows[0][1]
        except BaseException:
            self._reset()
            raise

    def records(self, kind):
        self._usable()
        self._reset()

        def rows():
            try:
                yield from self.journal._bounded_rows("kind=?", (kind,), self.budget, scope=self)
            finally:
                self._close_view()

        self.iterator = rows()
        return self.iterator


class ReadOnlyRecoveryJournal(RecoveryJournal):
    """Existing private ledger snapshot; a missing file never becomes zero rows."""

    def __init__(self, root, *, budget=None, index_bytes=None):
        self.root = root
        self.index_bytes = index_bytes
        self._native_views = []
        self._read_scope = None
        self._closed = False
        from scripts.execution_capacity.evidence_bounds import EvidenceBudget

        self.budget = budget if budget is not None else EvidenceBudget()
        _private_directory(root)
        path = root / "recovery.sqlite3"
        fd = _open_private(path, os.O_RDONLY)
        os.close(fd)
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        self.db.execute("PRAGMA query_only=ON")
        self.db.execute("BEGIN")

    def get(self, kind, identity):
        return self.bounded_get(kind, identity, self.budget)

    def records(self, kind):
        yield from self.bounded_records(kind, self.budget)

    def intent(self, kind, identity, body):
        raise ValueError("readonly original journal")

    def acknowledge(self, kind, identity, receipt):
        raise ValueError("readonly original journal")


class ObservedStorage:
    def __init__(self, delegate, journal):
        self.delegate, self.journal = delegate, journal
        self.pending = {}

    async def put_bytes(self, key, data):
        match = re.fullmatch(
            r"execution/(inputs|results)/([0-9a-f-]{36})/([0-9a-f]{64})\.json", key
        )
        if match is None or sha256(data).hexdigest() != match[3]:
            raise ValueError("unowned content-addressed object")
        kind = "run" if match[1] == "inputs" else "activity"
        parent = self.journal.parent(kind, match[2])
        self.journal.intent(
            "object",
            key,
            {
                "scope": parent["scope"],
                "parent_kind": kind,
                "parent": match[2],
                "sha256": match[3],
                "size": len(data),
            },
        )
        # Every physical attempt has its own receipt. A later successful put of
        # identical bytes must not launder an earlier response-lost upload.
        if len(self.pending) >= 5:
            raise RuntimeError("owned upload awaiter bound exceeded")
        upload = str(uuid4())
        self.journal.intent("upload", upload, {"key": key, "sha256": match[3], "size": len(data)})

        async def send():
            token = PORT_UPLOAD.set(upload)
            try:
                return await self.delegate.put_bytes(key, data)
            finally:
                PORT_UPLOAD.reset(token)

        task = asyncio.create_task(send())
        self.pending[task] = upload
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        await asyncio.shield(task)
        await self._ack_upload(task)

    async def _ack_upload(self, task):
        upload = self.pending[task]
        record = self.journal.parent("upload", upload)
        task.result()  # actual SDK completion; propagate its original error
        actual = await self.delegate.get_bytes(record["key"])
        if sha256(actual).hexdigest() != record["sha256"] or len(actual) != record["size"]:
            raise ValueError("object readback differs")
        receipt = {"sha256": record["sha256"], "size": record["size"]}
        self.journal.acknowledge("upload", upload, receipt)
        self.journal.acknowledge("object", record["key"], receipt)
        del self.pending[task]

    async def reconcile(self, key):
        record = self.journal.parent("object", key)
        try:
            actual = await self.delegate.get_bytes(key)
        except ObjectNotFoundError:
            return "uncertain_absent"
        if sha256(actual).hexdigest() != record["sha256"] or len(actual) != record["size"]:
            raise ValueError("recovered object differs")
        # Existing bytes prove current presence, not that an old SDK writer can
        # no longer complete. No delete or physical-clean receipt follows.
        return "retained_present"

    async def drain(self, budget_seconds=30):
        if self.pending:
            _, pending = await asyncio.wait(tuple(self.pending), timeout=budget_seconds)
            if pending:
                raise RuntimeError("owned upload completion remains uncertain")
            for task in tuple(self.pending):
                await self._ack_upload(task)
        if self.journal.db.execute(
            "SELECT count(*) FROM intents WHERE kind='upload' AND receipt IS NULL"
        ).fetchone()[0]:
            raise RuntimeError("persisted upload attempt remains uncertain")

    async def get_bytes(self, key):
        return await self.delegate.get_bytes(key)

    async def get_bounded_bytes(self, key, limit):
        return await self.delegate.get_bounded_bytes(key, limit)

    async def delete_bytes(self, key):
        raise ValueError("execution objects retained; no proven deletion authority")


class ObservedHandler:
    """Observe real envelopes before orchestrator submission; never rewrite them."""

    def __init__(self, delegate, journal):
        self.delegate, self.journal = delegate, journal

    def record(self, command):
        parent = self.journal.parent("run", command.stream_id)
        scope = "team:" + command.team_id if command.team_id else "user:" + command.owner_user_id
        if scope != parent["scope"]:
            raise ValueError("command owner differs")
        self.journal.intent("command", command.command_id, command.model_dump(mode="json"))

    async def handle(self, command):
        self.record(command)
        result = await self.delegate.handle(command)
        if result.status != "deferred":
            self.journal.acknowledge("command", command.command_id, result.model_dump(mode="json"))
        return result


class ObservedSink(ObservedHandler):
    async def receive(self, command, *, max_active_runs=0):
        self.record(command)
        # Preserve actual admission reservation and ceiling, including zero.
        return await self.delegate.receive(command, max_active_runs=max_active_runs)


class ObservedContent:
    def __init__(self, delegate, journal):
        self.delegate, self.journal = delegate, journal

    async def prepare(self, claim, command_id, command_type, payload, *, citations=()):
        parent = self.journal.parent("activity", claim.request.activity_id)
        if parent["run_id"] != claim.request.aggregate_id:
            raise ValueError("content parent differs")
        phase = "input" if command_type == "MarkActivityCallStarted" else "output"
        if command_type in {"MarkActivityCallStarted", "CompleteActivity"}:
            self.journal.intent(
                "content",
                f"{command_id}:{phase}",
                {
                    "scope": parent["scope"],
                    "run_id": claim.request.aggregate_id,
                    "activity_id": str(claim.request.activity_id),
                    "generation": claim.request.generation,
                    "claim_generation": claim.claim_generation,
                    "command_id": str(command_id),
                    "phase": phase,
                    "payload": payload,
                },
            )
        await self.delegate.prepare(claim, command_id, command_type, payload, citations=citations)
