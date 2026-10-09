"""Owned disposable indexes for exact bounded capacity validation.

This stores already validated payloads, never grants them evidence authority.
The caller must provide a quota derived from its complete input inventory.
SQLite on macOS cannot write through a held descriptor. Its pathname is only a
new random private scratch name; identity is checked around every transaction.
This detects replacement but assumes same-UID processes do not maliciously race
between checks. Authoritative source and retained-copy IO have no such exception.
"""

import contextlib
import json
import math
import os
import sqlite3
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

from scripts.acceptance.capacity_io import _directory

PAGE_BYTES = 4096
CACHE_BYTES = 32 * 1024 * 1024


def _identity(value):
    return value.st_dev, value.st_ino


def _numeric_compare(left, right):
    # JSON preserves arbitrary Python integers; SQLite REAL would round them.
    a, b = json.loads(left), json.loads(right)
    return (a > b) - (a < b)


class CapacityIndex:
    def __init__(self, *, quota_bytes, row_bytes=32 * 1024 * 1024, serialized_threads=False):
        if (
            type(quota_bytes) is not int
            or quota_bytes < 8 * PAGE_BYTES
            or type(row_bytes) is not int
            or row_bytes < 1
        ):
            raise ValueError("positive bounded index quota required")
        self.quota_bytes = quota_bytes // PAGE_BYTES * PAGE_BYTES
        self.row_bytes = row_bytes
        self.metrics = {"largest_payload_bytes": 0, "records": 0, "payload_bytes": 0}
        self._valid, self._closed, self._connection = True, False, None
        self._readers = set()
        self._parent_path = Path(tempfile.gettempdir()).resolve(strict=True)
        self._parent = _directory(self._parent_path)
        self._parent_identity = _identity(os.fstat(self._parent))
        self._name = "capacity-index-" + uuid4().hex
        self.root = self._parent_path / self._name
        self._directory = self._file = None
        try:
            os.mkdir(self._name, 0o700, dir_fd=self._parent)
            self._directory = os.open(
                self._name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent
            )
            self._directory_identity = _identity(os.fstat(self._directory))
            self._file = os.open(
                "index.sqlite",
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._directory,
            )
            self._file_identity = _identity(os.fstat(self._file))
            self._check()
            self._connection = sqlite3.connect(
                (self.root / "index.sqlite").as_uri() + "?mode=rw",
                uri=True,
                isolation_level=None,
                # The acquisition owner serializes every access with its RLock.
                # Normal validation sessions keep sqlite's single-thread guard.
                check_same_thread=not serialized_threads,
            )
            self._check()
            db = self._connection
            db.enable_load_extension(False)
            db.create_collation("EXACT_NUMBER", _numeric_compare)
            for statement in (
                "PRAGMA journal_mode=OFF",
                "PRAGMA mmap_size=0",
                "PRAGMA page_size=4096",
                f"PRAGMA max_page_count={self.quota_bytes // PAGE_BYTES}",
                f"PRAGMA cache_size=-{min(CACHE_BYTES, self.quota_bytes) // 1024}",
                "PRAGMA cache_spill=ON",
                "PRAGMA automatic_index=OFF",
                "PRAGMA temp_store=MEMORY",
                "PRAGMA trusted_schema=OFF",
            ):
                db.execute(statement).close()
            with self.transaction():
                for statement in (
                    "CREATE TABLE counts (stream TEXT PRIMARY KEY, length INTEGER NOT NULL) WITHOUT ROWID",
                    "CREATE TABLE records (stream TEXT, ordinal INTEGER, payload BLOB NOT NULL, PRIMARY KEY(stream,ordinal)) WITHOUT ROWID",
                    "CREATE TABLE identities (stream TEXT, kind TEXT, identity TEXT, ordinal INTEGER NOT NULL, PRIMARY KEY(stream,kind,identity)) WITHOUT ROWID",
                    "CREATE TABLE numbers (stream TEXT, value TEXT COLLATE EXACT_NUMBER, ordinal INTEGER, PRIMARY KEY(stream,value,ordinal)) WITHOUT ROWID",
                    "CREATE TABLE groups (stream TEXT, identity TEXT, ordinal INTEGER, payload BLOB NOT NULL, PRIMARY KEY(stream,identity,ordinal)) WITHOUT ROWID",
                    "CREATE TABLE ordered_groups (stream TEXT, identity TEXT, value TEXT COLLATE EXACT_NUMBER, ordinal INTEGER, payload BLOB NOT NULL, PRIMARY KEY(stream,identity,value,ordinal)) WITHOUT ROWID",
                ):
                    db.execute(statement).close()
            db.set_authorizer(self._authorize)
        except BaseException:
            self._valid = False
            self.close()
            raise

    @staticmethod
    def _authorize(action, first, second, database, trigger):
        forbidden = {
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_CREATE_VTABLE,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
            sqlite3.SQLITE_DROP_VTABLE,
        }
        if action in forbidden or (
            action == sqlite3.SQLITE_FUNCTION and second == "load_extension"
        ):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and (
            first not in {"page_count", "max_page_count", "cache_size", "mmap_size"}
            or second is not None
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _usable(self):
        if not self._valid or self._closed:
            raise ValueError("invalid disposable index session")

    def _check(self):
        try:
            parent = _directory(self._parent_path)
            try:
                if _identity(os.fstat(parent)) != self._parent_identity:
                    raise ValueError("scratch parent identity differs")
            finally:
                os.close(parent)
            directory = os.fstat(self._directory)
            named_directory = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
            file = os.fstat(self._file)
            named_file = os.stat("index.sqlite", dir_fd=self._directory, follow_symlinks=False)
            if (
                _identity(directory) != self._directory_identity
                or _identity(named_directory) != self._directory_identity
                or not stat.S_ISDIR(named_directory.st_mode)
                or named_directory.st_uid != os.geteuid()
                or stat.S_IMODE(named_directory.st_mode) != 0o700
                or _identity(file) != self._file_identity
                or _identity(named_file) != self._file_identity
                or not stat.S_ISREG(named_file.st_mode)
                or named_file.st_uid != os.geteuid()
                or stat.S_IMODE(named_file.st_mode) != 0o600
                or named_file.st_nlink != 1
            ):
                raise ValueError("scratch identity differs")
        except (OSError, ValueError):
            self._valid = False
            raise ValueError("scratch identity differs") from None

    @contextlib.contextmanager
    def transaction(self):
        self._usable()
        nested = self._connection.in_transaction
        if nested:
            try:
                yield
            except BaseException:
                self._valid = False
                raise
            return
        self._check()
        try:
            self._connection.execute("BEGIN").close()
            yield
            self._usable()
            self._check()
            self._connection.execute("COMMIT").close()
            self._check()
        except BaseException as error:
            self._valid = False
            with contextlib.suppress(sqlite3.Error):
                self._connection.execute("ROLLBACK").close()
            if isinstance(error, sqlite3.IntegrityError):
                raise ValueError("duplicate indexed identity") from None  # noqa: TRY004 - storage failure, not caller type
            if isinstance(error, sqlite3.Error):
                raise ValueError("disposable index quota or storage failure") from None  # noqa: TRY004 - storage failure, not caller type
            raise

    def _name_value(self, value):
        if type(value) is not str or not value or len(value) > self.row_bytes:
            raise ValueError("bounded index identity required")
        if len(value.encode()) > self.row_bytes:
            raise ValueError("bounded index identity required")

    def _payload(self, value):
        if type(value) is not bytes or len(value) > self.row_bytes:
            raise ValueError("bounded index row required")
        self.metrics["largest_payload_bytes"] = max(
            self.metrics["largest_payload_bytes"], len(value)
        )

    def _next(self, stream):
        row = self._connection.execute(
            "SELECT length FROM counts WHERE stream=?", (stream,)
        ).fetchone()
        ordinal = 0 if row is None else row[0]
        self._connection.execute(
            "INSERT INTO counts VALUES(?,?) ON CONFLICT(stream) DO UPDATE SET length=excluded.length",
            (stream, ordinal + 1),
        ).close()
        return ordinal

    def append(self, stream, payload, *, keys=None):
        self._name_value(stream)
        self._payload(payload)
        keys = {} if keys is None else keys
        for kind, identity in keys.items():
            self._name_value(kind)
            self._name_value(identity)
        with self.transaction():
            ordinal = self._next("r:" + stream)
            self._connection.execute(
                "INSERT INTO records VALUES(?,?,?)", (stream, ordinal, payload)
            ).close()
            for kind, identity in keys.items():
                self._connection.execute(
                    "INSERT INTO identities VALUES(?,?,?,?)", (stream, kind, identity, ordinal)
                ).close()
        self.metrics["records"] += 1
        self.metrics["payload_bytes"] += len(payload)
        return ordinal

    def count(self, stream):
        return self._count("r:" + stream)

    def count_numbers(self, stream):
        return self._count("n:" + stream)

    def _count(self, stream):
        row = self._read_one("SELECT length FROM counts WHERE stream=?", (stream,))
        return 0 if row is None else row[0]

    @contextlib.contextmanager
    def _read(self, statement, params):
        self._usable()
        self._check()
        cursor = None
        try:
            cursor = self._connection.execute(statement, params)
            self._readers.add(cursor)
            yield cursor
            self._usable()
            self._check()
        except sqlite3.Error:
            self._valid = False
            raise ValueError("disposable index storage failure") from None
        finally:
            if cursor is not None:
                self._readers.discard(cursor)
                # A caller can abandon a suspended finite generator. close()
                # has already released its cursor before closing SQLite.
                if not self._closed:
                    cursor.close()

    def _read_one(self, statement, params):
        with self._read(statement, params) as cursor:
            return cursor.fetchone()

    def rows(self, stream):
        with self._read(
            "SELECT payload FROM records WHERE stream=? ORDER BY ordinal", (stream,)
        ) as cursor:
            for row in cursor:
                self._usable()
                yield row[0]

    def identity_rows(self, stream, kind):
        """Enumerate complete payloads by the fixed full identity B-tree key."""
        self._name_value(stream)
        self._name_value(kind)
        with self._read(
            "SELECT r.payload FROM identities i JOIN records r ON r.stream=i.stream AND r.ordinal=i.ordinal WHERE i.stream=? AND i.kind=? ORDER BY i.identity",
            (stream, kind),
        ) as cursor:
            for row in cursor:
                self._usable()
                yield row[0]

    def find(self, stream, kind, identity):
        row = self._read_one(
            "SELECT r.payload FROM identities i JOIN records r ON r.stream=i.stream AND r.ordinal=i.ordinal WHERE i.stream=? AND i.kind=? AND i.identity=?",
            (stream, kind, identity),
        )
        return None if row is None else row[0]

    def group(self, stream, identity, payload):
        self._name_value(stream)
        self._name_value(identity)
        self._payload(payload)
        with self.transaction():
            ordinal = self._next("g:" + stream)
            self._connection.execute(
                "INSERT INTO groups VALUES(?,?,?,?)", (stream, identity, ordinal, payload)
            ).close()
        self.metrics["records"] += 1
        self.metrics["payload_bytes"] += len(payload)
        return ordinal

    def group_rows(self, stream, identity):
        self._name_value(stream)
        self._name_value(identity)
        with self._read(
            "SELECT payload FROM groups WHERE stream=? AND identity=? ORDER BY ordinal",
            (stream, identity),
        ) as cursor:
            for row in cursor:
                self._usable()
                yield row[0]

    def group_last(self, stream, identity):
        """Fixed lookup for derived last-write maps; all group rows stay retained."""
        self._name_value(stream)
        self._name_value(identity)
        row = self._read_one(
            "SELECT payload FROM groups WHERE stream=? AND identity=? ORDER BY ordinal DESC LIMIT 1",
            (stream, identity),
        )
        return None if row is None else row[0]

    def ordered_group(self, stream, identity, value, payload):
        """Stable exact-number order within one complete identity group."""
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            raise ValueError("exact finite numeric observation required")
        self._name_value(stream)
        self._name_value(identity)
        self._payload(payload)
        raw = json.dumps(value, allow_nan=False)
        with self.transaction():
            ordinal = self._next("o:" + stream)
            self._connection.execute(
                "INSERT INTO ordered_groups VALUES(?,?,?,?,?)",
                (stream, identity, raw, ordinal, payload),
            ).close()
        self.metrics["records"] += 1
        self.metrics["payload_bytes"] += len(payload)

    def ordered_group_count(self, stream, identity):
        return self._read_one(
            "SELECT COUNT(*) FROM ordered_groups WHERE stream=? AND identity=?", (stream, identity)
        )[0]

    def ordered_group_at(self, stream, identity, ordinal):
        if type(ordinal) is not int or ordinal < 0:
            raise IndexError(ordinal)
        row = self._read_one(
            "SELECT payload FROM ordered_groups WHERE stream=? AND identity=? ORDER BY value COLLATE EXACT_NUMBER,ordinal LIMIT 1 OFFSET ?",
            (stream, identity, ordinal),
        )
        if row is None:
            raise IndexError(ordinal)
        return row[0]

    def ordered_group_rows(self, stream, identity):
        with self._read(
            "SELECT payload FROM ordered_groups WHERE stream=? AND identity=? ORDER BY value COLLATE EXACT_NUMBER,ordinal",
            (stream, identity),
        ) as cursor:
            for row in cursor:
                self._usable()
                yield row[0]

    def number(self, stream, value):
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            raise ValueError("exact finite numeric observation required")
        self._name_value(stream)
        raw = json.dumps(value, allow_nan=False)
        self._payload(raw.encode())
        with self.transaction():
            ordinal = self._next("n:" + stream)
            self._connection.execute(
                "INSERT INTO numbers VALUES(?,?,?)", (stream, raw, ordinal)
            ).close()

    def rank(self, stream, rank):
        if type(rank) is not int or not 1 <= rank <= self.count_numbers(stream):
            raise ValueError("numeric rank outside complete population")
        row = self._read_one(
            "SELECT value FROM numbers WHERE stream=? ORDER BY value COLLATE EXACT_NUMBER,ordinal LIMIT 1 OFFSET ?",
            (stream, rank - 1),
        )
        return json.loads(row[0])

    def query_plans_use_declared_indexes(self):
        self._usable()
        queries = (
            (
                "SELECT payload FROM ordered_groups WHERE stream=? AND identity=? ORDER BY value COLLATE EXACT_NUMBER,ordinal",
                ("sample", "identity"),
            ),
            (
                "SELECT payload FROM groups WHERE stream=? AND identity=? ORDER BY ordinal DESC LIMIT 1",
                ("sample", "identity"),
            ),
            (
                "SELECT payload FROM groups WHERE stream=? AND identity=? ORDER BY ordinal",
                ("sample", "identity"),
            ),
            (
                "SELECT r.payload FROM identities i JOIN records r ON r.stream=i.stream AND r.ordinal=i.ordinal WHERE i.stream=? AND i.kind=? ORDER BY i.identity",
                ("sample", "key"),
            ),
            ("SELECT payload FROM records WHERE stream=? ORDER BY ordinal", ("sample",)),
            (
                "SELECT value FROM numbers WHERE stream=? ORDER BY value COLLATE EXACT_NUMBER,ordinal LIMIT 1 OFFSET ?",
                ("sample", 0),
            ),
            (
                "SELECT r.payload FROM identities i JOIN records r ON r.stream=i.stream AND r.ordinal=i.ordinal WHERE i.stream=? AND i.kind=? AND i.identity=?",
                ("sample", "event", "id"),
            ),
        )
        for query, params in queries:
            with self._read("EXPLAIN QUERY PLAN " + query, params) as cursor:
                for row in cursor:
                    if "TEMP" in row[3] or "AUTOMATIC" in row[3] or "SCAN" in row[3]:
                        return False
        return True

    def __enter__(self):
        self._usable()
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self._valid = False
        self.close()

    def close(self):
        if self._closed:
            return
        owned = False
        try:
            if self._directory is not None and self._file is not None:
                try:
                    self._check()
                    owned = True
                except ValueError:
                    pass
            if self._connection is not None:
                for cursor in tuple(self._readers):
                    cursor.close()
                self._readers.clear()
                self._connection.close()
            if owned:
                try:
                    self._check()
                except ValueError:
                    owned = False
            if owned and self._valid:
                os.unlink("index.sqlite", dir_fd=self._directory)
                os.rmdir(self._name, dir_fd=self._parent)
            elif owned:
                data = json.dumps(
                    {"state": "invalid", "quota_bytes": self.quota_bytes, **self.metrics},
                    separators=(",", ":"),
                ).encode()
                fd = os.open(
                    "failure.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._directory,
                )
                with os.fdopen(fd, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                os.fsync(self._directory)
        finally:
            for fd in (self._file, self._directory, self._parent):
                if fd is not None:
                    os.close(fd)
            self._closed = True
