"""Durable acquisition occurrences, separate from disposable lookup scratch.

An unsealed prefix is retained but cannot authorize original evidence. Each
begin is durable before acquisition; completion captures the final observation,
in begin order even when concurrent acquisitions complete in another order.
Current individual bodies obey the caller's row ceiling. This is the acquisition
lifecycle primitive, not yet the full shared-body private export reader.
"""

import base64
import contextlib
import os
import stat
import threading
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from hashlib import sha256
from pathlib import Path
from uuid import UUID
from weakref import WeakValueDictionary

from scripts.acceptance.capacity_c2c_models import OPERAND_FAMILIES
from scripts.acceptance.capacity_index import CapacityIndex
from scripts.acceptance.capacity_io import _directory, strict_json
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError
from scripts.execution_capacity.original_segments import DEFAULT_CHUNK_BYTES, OriginalSegments

BUFFER = 64 * 1024
FAMILIES = frozenset({"cleanup", "sql", "objects", "transports"}) | {
    "operand:" + name for name in OPERAND_FAMILIES
}


def _identity(value):
    return value.st_dev, value.st_ino


def _stamp(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return call


def _write(fd, raw):
    remaining = memoryview(raw)
    while remaining:
        size = os.write(fd, remaining)
        if size <= 0:
            raise OSError("incomplete original write")
        remaining = remaining[size:]


def _typed_parts(value, budget, depth=0):
    """Encode one bounded node at a time without an intermediate typed graph."""
    if depth > 64:
        raise ValueError("original nesting exceeds bound")
    budget.reserve(64, rows=1)
    if isinstance(value, dict):
        yield b'["dict",['
        for ordinal, (key, child) in enumerate(value.items()):
            if not isinstance(key, str):
                raise TypeError("original dictionary keys must be strings")
            if ordinal:
                yield b","
            yield b"["
            budget.check(len(key) * 12 + 2, rows=0, largest=len(key) * 12 + 2)
            yield encode(key)
            yield b","
            yield from _typed_parts(child, budget, depth + 1)
            yield b"]"
        yield b"]]"
    elif isinstance(value, (list, tuple)):
        yield b'["list",['
        for ordinal, child in enumerate(value):
            if ordinal:
                yield b","
            yield from _typed_parts(child, budget, depth + 1)
        yield b"]]"
    else:
        tag = "scalar"
        if type(value) is bytes:
            # Base64 conversion is prepaid before allocating either copy.
            size = ((len(value) + 2) // 3) * 4
            budget.reserve(size * 2, rows=0, largest=size)
            value, tag = base64.b64encode(value).decode("ascii"), "bytes"
        elif type(value) in (UUID, Decimal, date, datetime):
            tag = {UUID: "uuid", Decimal: "decimal", date: "date", datetime: "datetime"}[
                type(value)
            ]
            value = value.isoformat() if isinstance(value, date) else str(value)
        elif value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError("unsupported original scalar")
        if isinstance(value, str):
            budget.check(len(value) * 12 + 32, rows=0, largest=len(value) * 12 + 32)
        yield encode([tag, value])


def _graph_parts(value, budget, *, owner=None, edges=None):
    """One bounded held root, with IDs local to its logical occurrence.

    The memo holds strong references, so Python identity cannot be reused while
    encoding. It is discarded for each root, including equal physical bodies.
    This adapter cannot consume unbounded cursor collections.
    """
    seen, active, heights = {}, set(), {}
    nodes = 0

    def visit(item, depth=0):
        nonlocal nodes
        if depth > 64:
            raise ValueError("original nesting exceeds bound")
        budget.reserve(192, rows=1)
        nodes += 1
        from scripts.execution_capacity.original_collections import (
            BaseCollectionRows,
            CollectionRows,
            CollectionWriter,
            PlainBaseCollectionRows,
            PlainCollectionRows,
        )
        from scripts.execution_capacity.original_dictionaries import (
            BaseDictionaryRows,
            DictionaryRows,
            DictionaryWriter,
            PlainBaseDictionaryRows,
            PlainDictionaryRows,
        )

        if type(item) in (CollectionWriter, DictionaryWriter):
            raise ValueError("collection must be complete before reference")
        dictionary = type(item) in (
            DictionaryRows,
            PlainDictionaryRows,
            BaseDictionaryRows,
            PlainBaseDictionaryRows,
        )
        collection = dictionary or type(item) in (
            CollectionRows,
            PlainCollectionRows,
            BaseCollectionRows,
            PlainBaseCollectionRows,
        )
        plain_collection = type(item) in (
            PlainCollectionRows,
            PlainBaseCollectionRows,
            PlainDictionaryRows,
            PlainBaseDictionaryRows,
        )
        container = (
            collection
            or isinstance(item, (dict, list, tuple))
            or is_dataclass(item)
            or hasattr(type(item), "model_fields")
        )
        if not container:
            yield from _typed_parts(item, budget)
            return 0
        if collection:
            if owner is None or edges is None:
                raise ValueError("owned collection reference required")
            metadata = owner._collection_metadata(item)
        identity = (
            (
                item.body_descriptor["namespace"],
                item.producer_ordinal,
                item.logical_node if plain_collection else None,
            )
            if collection
            else id(item)
        )
        if identity in active:
            raise ValueError("cyclic original graph")
        if identity in seen:
            node, held = seen[identity]
            if (not collection and held is not item) or depth + heights[node] > 64:
                raise ValueError("original reference nesting exceeds bound")
            yield encode(["ref", node])
            return heights[node]
        node = len(seen)
        seen[identity] = node, item
        active.add(identity)
        if collection:
            if owner is None or edges is None:
                raise ValueError("owned collection reference required")
            metadata = owner._collection_metadata(item)
            if depth + metadata["height"] > 64:
                raise ValueError("original collection nesting exceeds bound")
            edge = [node, item.producer_ordinal]
            if dictionary:
                edge.append("dict")
            declaration = ["dict-body" if dictionary else "list-body", node, item.body_descriptor]
            if plain_collection:
                edge.append("plain-json-v1")
                declaration.append("plain-json-v1")
            edges.append(edge)
            yield encode(declaration)
            active.remove(identity)
            heights[node] = metadata["height"]
            return metadata["height"]
        if is_dataclass(item):
            members = ((field.name, getattr(item, field.name)) for field in fields(item))
            kind = "dict"
        elif hasattr(type(item), "model_fields"):
            members = ((key, getattr(item, key)) for key in type(item).model_fields)
            kind = "dict"
        elif isinstance(item, dict):
            members, kind = item.items(), "dict"
        else:
            members, kind = enumerate(item), "list"
        yield b"[" + encode(kind) + b"," + str(node).encode() + b",["
        height = 0
        for ordinal, (key, child) in enumerate(members):
            if ordinal:
                yield b","
            if kind == "dict":
                if not isinstance(key, str):
                    raise ValueError("original dictionary keys must be strings")
                budget.check(len(key) * 12 + 2, rows=0, largest=len(key) * 12 + 2)
                yield b"[" + encode(key) + b","
            child_height = yield from visit(child, depth + 1)
            height = max(height, child_height + 1)
            if kind == "dict":
                yield b"]"
        yield b"]]"
        active.remove(identity)
        heights[node] = height
        return height

    yield b'["graph",2,'
    yield from visit(value)
    yield b"," + str(nodes).encode() + b"]"


def _decode(raw, budget, *, resolve=None, stats=None, _borrowed_prepaid=False):
    if not _borrowed_prepaid:
        budget.reserve(len(raw) * 64, rows=0, largest=len(raw))
    document = strict_json(raw)
    if (
        type(document) is not list
        or len(document) != 4
        or document[:2] != ["graph", 2]
        or type(document[1]) is not int
        or type(document[3]) is not int
        or not 1 <= document[3] <= budget.rows_limit
    ):
        raise ValueError("invalid original graph closure")
    declared, completed = set(), {}
    nodes = 0

    def visit(node, depth=0):
        nonlocal nodes
        budget.reserve(128, rows=1)
        nodes += 1
        if (
            depth > 64
            or nodes > document[3]
            or type(node) is not list
            or len(node) not in (2, 3, 4)
            or type(node[0]) is not str
        ):
            raise ValueError("invalid typed original node")
        if node[0] == "ref":
            if len(node) != 2 or type(node[1]) is not int or node[1] not in completed:
                raise ValueError("invalid original logical reference")
            result, height = completed[node[1]]
            if depth + height > 64:
                raise ValueError("original reference nesting exceeds bound")
            return result, height
        if node[0] in ("list-body", "dict-body"):
            if (
                len(node) not in (3, 4)
                or (len(node) == 4 and node[3] != "plain-json-v1")
                or type(node[1]) is not int
                or node[1] != len(declared)
                or resolve is None
            ):
                raise ValueError("invalid original collection declaration")
            result, height = resolve(
                node[1],
                node[2],
                node[3] if len(node) == 4 else None,
                "dict" if node[0] == "dict-body" else "list",
            )
            if depth + height > 64:
                raise ValueError("original collection nesting exceeds bound")
            declared.add(node[1])
            completed[node[1]] = result, height
            return result, height
        if node[0] in ("dict", "list"):
            if (
                len(node) != 3
                or type(node[1]) is not int
                or node[1] != len(declared)
                or type(node[2]) is not list
            ):
                raise ValueError("invalid original container declaration")
            kind, ordinal, members = node
            declared.add(ordinal)
            result = {} if kind == "dict" else []
            height = 0
            for member in members:
                if kind == "dict":
                    if (
                        type(member) is not list
                        or len(member) != 2
                        or type(member[0]) is not str
                        or member[0] in result
                    ):
                        raise ValueError("invalid typed original member")
                    child, child_height = visit(member[1], depth + 1)
                    result[member[0]] = child
                else:
                    child, child_height = visit(member, depth + 1)
                    result.append(child)
                height = max(height, child_height + 1)
            completed[ordinal] = result, height
            return result, height
        if len(node) != 2:
            raise ValueError("invalid original scalar declaration")
        tag, value = node
        if tag == "scalar" and (value is None or type(value) in (str, bool, int, float)):
            return value, 0
        converters = {
            "uuid": UUID,
            "decimal": Decimal,
            "date": date.fromisoformat,
            "datetime": datetime.fromisoformat,
        }
        if tag in converters and type(value) is str:
            return converters[tag](value), 0
        if tag == "bytes" and type(value) is str:
            budget.reserve(len(value) * 2, rows=0, largest=len(value))
            return base64.b64decode(value, validate=True), 0
        raise ValueError("unknown typed original node")

    result, height = visit(document[2])
    if nodes != document[3] or len(declared) != len(completed):
        raise ValueError("original graph counts differ")
    if stats is not None:
        stats.update(nodes=nodes, height=height)
    return result


@dataclass(frozen=True)
class _RootScope:
    owner: object
    identity: tuple


@dataclass(frozen=True)
class _Token:
    owner: object
    family: str
    ordinal: int


class _Occurrences(Sequence):
    def __init__(self, owner, family, selection=None):
        self.owner, self.family, self.selection = owner, family, selection

    def __len__(self):
        with self.owner._lock:
            return (
                self.owner.index.count("begin:" + self.family)
                if self.selection is None
                else len(self.selection)
            )

    def __getitem__(self, ordinal):
        if isinstance(ordinal, slice):
            indices = range(len(self)) if self.selection is None else self.selection
            return _Occurrences(self.owner, self.family, indices[ordinal])
        if type(ordinal) is not int:
            raise TypeError("original ordinal required")
        if ordinal < 0:
            ordinal += len(self)
        if not 0 <= ordinal < len(self):
            raise IndexError(ordinal)
        if self.selection is not None:
            ordinal = self.selection[ordinal]
        with self.owner._lock:
            raw = self.owner.index.find("end:" + self.family, "ordinal", str(ordinal))
            if raw is None:
                raise ValueError("pending original occurrence")
            return self.owner._record_value(strict_json(raw))


class OriginalJournal:
    @classmethod
    def create(cls, root, *, budget, index_bytes, chunk_bytes=DEFAULT_CHUNK_BYTES):
        return cls(
            root, budget=budget, index_bytes=index_bytes, creating=True, chunk_bytes=chunk_bytes
        )

    @classmethod
    def open(cls, root, *, budget, index_bytes, verified_base=None):
        result = cls(root, budget=budget, index_bytes=index_bytes, creating=False)
        try:
            if verified_base is not None:
                result.bind_verified_base(verified_base)
            result._rebuild()
            return result
        except BaseException:
            result.valid = False
            result.close()
            raise

    def __init__(self, root, *, budget, index_bytes, creating, chunk_bytes=DEFAULT_CHUNK_BYTES):
        self._lock = threading.RLock()
        self._cursor_nodes = WeakValueDictionary()
        self._root_scopes = WeakValueDictionary()
        self._plain_nodes = WeakValueDictionary()
        self._next_plain_node = 0
        self.root, self.budget = Path(root).absolute(), budget
        self.valid, self.closed, self.sealed = True, False, False
        self.parent = self.directory = None
        self.body_segments = self.log_segments = None
        self.chunk_bytes = chunk_bytes
        self.index = None
        self._index_workspace = None
        self.binding = None
        self.manifest = None
        self.native_bodies = []
        self.verified_base = self._base_evidence = self._base_source = None
        self.index_bytes = index_bytes
        self.records = self.body_bytes = self.log_bytes = 0
        self.body_hash, self.log_hash = sha256(), sha256()
        try:
            self._index_workspace = budget.reserve_workspace(index_bytes)
            self.index = CapacityIndex(
                quota_bytes=index_bytes, row_bytes=budget.row_limit, serialized_threads=True
            )
            self.parent = _directory(self.root.parent)
            self.parent_identity = _identity(os.fstat(self.parent))
            if creating:
                os.mkdir(self.root.name, 0o700, dir_fd=self.parent)
            self.directory = os.open(
                self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent
            )
            self.directory_identity = _identity(os.fstat(self.directory))
            self._check()
            if creating:
                self.body_segments = OriginalSegments(
                    self, "body", chunk_bytes=chunk_bytes, creating=True
                )
                self.log_segments = OriginalSegments(
                    self, "log", chunk_bytes=chunk_bytes, creating=True
                )
                os.fsync(self.directory)
                os.fsync(self.parent)
        except BaseException:
            self.valid = False
            self.close()
            raise

    def _check(self):
        parent = _directory(self.root.parent)
        try:
            named = os.stat(self.root.name, dir_fd=self.parent, follow_symlinks=False)
            if (
                _identity(os.fstat(parent)) != self.parent_identity
                or _identity(named) != self.directory_identity
                or _identity(os.fstat(self.directory)) != self.directory_identity
                or not stat.S_ISDIR(named.st_mode)
                or stat.S_IMODE(named.st_mode) != 0o700
                or named.st_uid != os.geteuid()
            ):
                raise ValueError("original directory identity differs")
        finally:
            os.close(parent)

    def _file(self, name, flags):
        self._check()
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.directory)
        try:
            self._check_file(name, fd)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _check_file(self, name, fd):
        self._check()
        held, named = os.fstat(fd), os.stat(name, dir_fd=self.directory, follow_symlinks=False)
        if (
            _identity(held) != _identity(named)
            or not stat.S_ISREG(held.st_mode)
            or held.st_nlink != 1
            or held.st_uid != os.geteuid()
            or stat.S_IMODE(held.st_mode) != 0o600
        ):
            raise ValueError("original file identity or mode differs")

    def _usable(self, *, writing=False):
        if self.closed:
            raise ValueError("original owner closed")
        if not self.valid:
            raise ValueError("original owner invalid")
        if writing and self.sealed:
            raise ValueError("original owner sealed")

    def _store(self, value, *, edges=None):
        # Candidate bytes are owned temporary data until the occurrence commits.
        # Compare the complete typed encoding before sharing; a digest only
        # locates one candidate. Failed candidates remain as incomplete evidence.
        pending = self._file("body.pending", os.O_RDWR | os.O_CREAT | os.O_EXCL)
        hashed, size, complete = sha256(), 0, False
        try:
            for part in _graph_parts(value, self.budget, owner=self, edges=edges):
                size += len(part)
                if size > self.budget.row_limit:
                    raise EvidenceQuotaError("original body row quota exceeded")
                self.budget.reserve(len(part) * 2, rows=0)
                for start in range(0, len(part), BUFFER):
                    _write(pending, part[start : start + BUFFER])
                hashed.update(part)
            os.fsync(pending)
            self._check_file("body.pending", pending)
            pending_stamp = _stamp(os.fstat(pending))
            digest = hashed.hexdigest()
            candidate = self.index.find("bodies", "digest", self._lookup_key(digest))
            self.budget.reserve(2 * min(BUFFER, size), rows=0)
            if candidate is not None:
                saved = strict_json(candidate)
                matched = saved["bytes"] == size and self._equal_body(pending, saved)
                if _stamp(os.fstat(pending)) != pending_stamp:
                    raise ValueError("original candidate changed")
                if matched:
                    complete = True
                    return saved
            descriptor = {
                "namespace": "local",
                "encoding": 2,
                "offset": self.body_bytes,
                "bytes": size,
                "sha256": digest,
            }
            offset, copied_hash = 0, sha256()
            while offset < size:
                part = os.pread(pending, min(BUFFER, size - offset), offset)
                if not part:
                    raise ValueError("original candidate truncated")
                self.body_segments.write(part)
                self.body_bytes += len(part)
                self.body_hash.update(part)
                copied_hash.update(part)
                offset += len(part)
            self.body_segments.sync()
            self._check_file("body.pending", pending)
            if _stamp(os.fstat(pending)) != pending_stamp or copied_hash.hexdigest() != digest:
                raise ValueError("original candidate changed")
            self._remember_body(descriptor)
            complete = True
            return descriptor
        finally:
            try:
                if complete:
                    self._check_file("body.pending", pending)
                    os.unlink("body.pending", dir_fd=self.directory)
                    os.fsync(self.directory)
            finally:
                os.close(pending)

    @staticmethod
    def _lookup_key(digest):
        return digest

    def _remember_body(self, descriptor):
        key = self._lookup_key(descriptor["sha256"])
        keys = {"offset": str(descriptor["offset"])}
        if self.index.find("bodies", "digest", key) is None:
            keys["digest"] = key
        self.index.append("bodies", encode(descriptor), keys=keys)

    def _equal_body(self, pending, descriptor):
        offset, hashed = 0, sha256()
        while offset < descriptor["bytes"]:
            length = min(BUFFER, descriptor["bytes"] - offset)
            original = self.body_segments.read(length, descriptor["offset"] + offset)
            candidate = os.pread(pending, length, offset)
            if len(original) != length or len(candidate) != length or original != candidate:
                return False
            hashed.update(original)
            offset += length
        if hashed.hexdigest() != descriptor["sha256"]:
            raise ValueError("original body changed during typed equality")
        return True

    def _emit(self, kind, family, ordinal, value):
        self._usable(writing=True)
        try:
            edges = []
            body = self._store(value, edges=edges)
            self._commit_record(kind, family, ordinal, body, edges)
        except BaseException:
            self.valid = False
            raise

    def _commit_record(self, kind, family, ordinal, body, edges=()):
        try:
            record = {
                "sequence": self.records,
                "kind": kind,
                "family": family,
                "ordinal": ordinal,
                "body": body,
            }
            if edges:
                record["edges"] = edges
                self._record_value(record)
            self.budget.charge(record)
            raw = encode(record) + b"\n"
            self.budget.reserve(len(raw), rows=0, largest=len(raw))
            self.log_segments.write(raw)
            self.log_segments.sync()
            self.records += 1
            self.log_bytes += len(raw)
            self.log_hash.update(raw)
            self._index_row(record)
        except BaseException:
            self.valid = False
            raise

    @_serialized
    def begin(self, family, metadata):
        self._usable(writing=True)
        if family not in FAMILIES:
            raise ValueError("unknown original family")
        ordinal = self.index.count("begin:" + family)
        self._emit("begin", family, ordinal, metadata)
        return _Token(self, family, ordinal)

    @_serialized
    def complete(self, token, value):
        self._usable(writing=True)
        self.ordinal(token)
        if self.index.find("end:" + token.family, "ordinal", str(token.ordinal)) is not None:
            raise ValueError("original occurrence already completed")
        self._emit("end", token.family, token.ordinal, value)

    @_serialized
    def note(self, token, value):
        self._usable(writing=True)
        self.ordinal(token)
        if self.index.find("end:" + token.family, "ordinal", str(token.ordinal)) is not None:
            raise ValueError("original occurrence already completed")
        self._emit("note", token.family, token.ordinal, value)

    def notes(self, family, ordinal):
        with self._lock:
            self._usable()
            if family not in FAMILIES or type(ordinal) is not int or ordinal < 0:
                raise ValueError("owned original ordinal required")
            for raw in self.index.rows("note:" + family + ":" + str(ordinal)):
                yield self._body(strict_json(raw)["body"])

    def _index_row(self, row):
        stream = row["kind"] + ":" + row["family"]
        if row["kind"] == "note":
            stream += ":" + str(row["ordinal"])
            ordinal = self.index.count(stream)
        else:
            ordinal = row["ordinal"]
        self.index.append(stream, encode(row), keys={"ordinal": str(ordinal)})

    @_serialized
    def append(self, family, value):
        token = self.begin(family, {})
        self.complete(token, value)
        return token.ordinal

    @_serialized
    def sequence(self, family):
        self._usable()
        if family not in FAMILIES:
            raise ValueError("unknown original family")
        return _Occurrences(self, family)

    def owns_sequence(self, value, family):
        return (
            type(value) is _Occurrences
            and value.owner is self
            and value.family == family
            and value.selection is None
        )

    def roots(self):
        self._usable()
        if not self.sealed or not self.manifest["original_roots"]:
            raise ValueError("complete original root closure required")
        return {
            "cleanup": self.sequence("cleanup")[0],
            "operands": {
                name: self.sequence("operand:" + name) for name in sorted(OPERAND_FAMILIES)
            },
            **{name: self.sequence(name) for name in ("sql", "objects", "transports")},
        }

    @_serialized
    def ordinal(self, token, *, family=None):
        self._usable()
        if (
            type(token) is not _Token
            or token.owner is not self
            or (family is not None and token.family != family)
            or self.index.find("begin:" + token.family, "ordinal", str(token.ordinal)) is None
        ):
            raise ValueError("owned original token required")
        return token.ordinal

    @_serialized
    def _body_raw(self, descriptor):
        self._usable()
        if (
            type(descriptor) is not dict
            or set(descriptor) != {"namespace", "encoding", "offset", "bytes", "sha256"}
            or descriptor["namespace"] != "local"
            or type(descriptor["encoding"]) is not int
            or descriptor["encoding"] != 2
        ):
            raise ValueError("closed original body reference required")
        size, offset = descriptor["bytes"], descriptor["offset"]
        if (
            type(size) is not int
            or not 0 < size <= self.budget.row_limit
            or type(offset) is not int
            or offset < 0
            or offset + size > self.body_segments.length
        ):
            raise ValueError("invalid original body range")
        self.budget.reserve(size * 2, rows=0, largest=size)
        raw = self.body_segments.read(size, offset)
        if len(raw) != size or sha256(raw).hexdigest() != descriptor["sha256"]:
            raise ValueError("original body changed")
        return raw

    @_serialized
    def _body(self, descriptor, *, resolve=None, stats=None):
        return _decode(self._body_raw(descriptor), self.budget, resolve=resolve, stats=stats)

    def _validate_collection_parent_body(self, descriptor):
        # This verifier consumes only a fixed metadata dict and returns no
        # graph or cursor. The encoded index row has its own owner quota.
        raw = self._body_raw(descriptor)
        workspace = self.budget.reserve_workspace(len(raw) * 64, largest=len(raw))
        value = None
        try:
            value = _decode(raw, self.budget, _borrowed_prepaid=True)
            self._validate_collection_parent(value)
        except BaseException:
            workspace.promote()
            raise
        else:
            value = None
            workspace.release()

    def _validate_collection_parent(self, value):
        if (
            type(value) is not dict
            or set(value)
            not in ({"family", "ordinal", "slot"}, {"family", "ordinal", "slot", "kind"})
            or ("kind" in value and value["kind"] != "dict")
            or value["family"] not in FAMILIES | {"native-imports"}
            or type(value["ordinal"]) is not int
            or value["ordinal"] < 0
            or type(value["slot"]) is not str
            or not value["slot"]
        ):
            raise ValueError("owned collection parent required")
        family, ordinal = value["family"], str(value["ordinal"])
        if (
            self.index.find("begin:" + family, "ordinal", ordinal) is None
            or self.index.find("end:" + family, "ordinal", ordinal) is not None
        ):
            raise ValueError("pending collection parent required")
        key = encode({key: value[key] for key in ("family", "ordinal", "slot")}).decode()
        if self.index.find("collection-slots", "slot", key) is not None:
            raise ValueError("duplicate collection parent slot")
        self.index.append("collection-slots", encode(value), keys={"slot": key})

    @_serialized
    def begin_collection(self, parent, slot):
        from scripts.execution_capacity.original_collections import CollectionWriter

        self._usable(writing=True)
        self.ordinal(parent)
        metadata = {"family": parent.family, "ordinal": parent.ordinal, "slot": slot}
        self._validate_collection_parent(metadata)
        ordinal = self.index.count("begin:collections")
        self._emit("begin", "collections", ordinal, metadata)
        return CollectionWriter(self, _Token(self, "collections", ordinal))

    @_serialized
    def begin_dictionary(self, parent, slot):
        from scripts.execution_capacity.original_dictionaries import DictionaryWriter

        self._usable(writing=True)
        self.ordinal(parent)
        metadata = {
            "family": parent.family,
            "ordinal": parent.ordinal,
            "slot": slot,
            "kind": "dict",
        }
        self._validate_collection_parent(metadata)
        ordinal = self.index.count("begin:collections")
        self._emit("begin", "collections", ordinal, metadata)
        return DictionaryWriter(self, _Token(self, "collections", ordinal))

    def _register_plain_node(self, value):
        self._usable()
        self.budget.reserve(256, rows=1, largest=(len(self._plain_nodes) + 1) * 256)
        ordinal = self._next_plain_node
        self._next_plain_node += 1
        object.__setattr__(value, "logical_node", ("conversion", ordinal))
        self._plain_nodes[ordinal] = value

    def _root_scope(self, identity):
        self._usable()
        if (
            type(identity) is not tuple
            or not identity
            or identity[0] not in ("record", "producer")
            or (
                identity[0] == "record"
                and (len(identity) != 2 or type(identity[1]) is not int or identity[1] < 0)
            )
            or (
                identity[0] == "producer"
                and (
                    len(identity) != 3
                    or identity[1] not in ("local", "verified-base")
                    or type(identity[2]) is not int
                    or identity[2] < 0
                )
            )
        ):
            raise ValueError("closed original root scope required")
        saved = self._root_scopes.get(identity)
        if saved is None:
            self.budget.reserve(256, rows=1, largest=(len(self._root_scopes) + 1) * 256)
            saved = _RootScope(self, identity)
            self._root_scopes[identity] = saved
        return saved

    def _checked_scope(self, scope):
        self._usable()
        if (
            type(scope) is not _RootScope
            or scope.owner is not self
            or self._root_scopes.get(scope.identity) is not scope
        ):
            raise ValueError("foreign original logical root scope")
        return scope

    def _finite_cursor(self, cursor, ordinal, count, descriptor, scope, logical_node=None):
        self._checked_scope(scope)
        self.budget.reserve(256, rows=1, largest=256)
        candidate = cursor(self, ordinal, count, descriptor, scope, logical_node)
        self._collection_metadata(candidate)
        key = scope.identity, cursor, descriptor["namespace"], ordinal, logical_node
        saved = self._cursor_nodes.get(key)
        if saved is None:
            self.budget.reserve(256, rows=1, largest=(len(self._cursor_nodes) + 1) * 256)
            self._cursor_nodes[key] = candidate
            return candidate
        self._collection_metadata(saved)
        if saved.length != count or saved.body_descriptor != descriptor:
            raise ValueError("original logical node snapshot differs")
        return saved

    def _collection_metadata(self, value):
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

        self._usable()
        if value.scope is not None:
            self._checked_scope(value.scope)
        base_metadata = None
        if (
            type(value.producer_ordinal) is not int
            or value.producer_ordinal < 0
            or type(value.length) is not int
            or value.length < 0
        ):
            raise ValueError("original collection snapshot count/type differs")
        if type(value) in (
            BaseCollectionRows,
            PlainBaseCollectionRows,
            BaseDictionaryRows,
            PlainBaseDictionaryRows,
        ):
            from scripts.execution_capacity.original_base_refs import metadata

            base_metadata = metadata(self, value)
        if type(value) in (
            PlainCollectionRows,
            PlainBaseCollectionRows,
            PlainDictionaryRows,
            PlainBaseDictionaryRows,
        ):
            node = value.logical_node
            if type(node) is not tuple or not node or node[0] not in ("conversion", "record"):
                raise ValueError("original plain logical node required")
            if node[0] == "conversion" and (
                len(node) != 2 or self._plain_nodes.get(node[1]) is not value
            ):
                raise ValueError("foreign original plain conversion node")
            if node[0] == "record" and (
                len(node) != 3
                or value.scope is None
                or any(type(n) is not int or n < 0 for n in node[1:])
            ):
                raise ValueError("original plain record node differs")
        if base_metadata is not None:
            return base_metadata
        if (
            type(value)
            not in (CollectionRows, PlainCollectionRows, DictionaryRows, PlainDictionaryRows)
            or value.owner is not self
        ):
            raise ValueError("foreign original collection owner")
        saved = self.index.find("collection-meta", "ordinal", str(value.producer_ordinal))
        if saved is None:
            raise ValueError("complete collection required")
        completed = self.index.find("end:collections", "ordinal", str(value.producer_ordinal))
        if completed is None:
            raise ValueError("durably complete collection required")
        metadata = strict_json(saved)
        dictionary = type(value) in (DictionaryRows, PlainDictionaryRows)
        if (metadata.get("kind") == "dict") != dictionary:
            raise ValueError("original collection kind differs")
        if strict_json(completed)["body"] != metadata["body"]:
            raise ValueError("original collection completion differs")
        if metadata["body"] != value.body_descriptor or metadata["count"] != value.length:
            raise ValueError("original collection snapshot differs")
        return metadata

    def _record_value(self, record, *, stats=None, scope=None, _discarded_leaf=False):
        from scripts.execution_capacity.original_collections import (
            CollectionRows,
            PlainCollectionRows,
        )

        scope = (
            self._root_scope(("record", record["sequence"]))
            if scope is None
            else self._checked_scope(scope)
        )
        edges = record.get("edges", [])
        if type(edges) is not list:
            raise ValueError("closed original collection edges required")
        if _discarded_leaf and edges:
            raise ValueError("discarded original value must have no collection edges")
        consumed = 0

        def resolve(node, descriptor, mode, kind):
            nonlocal consumed
            if consumed >= len(edges):
                raise ValueError("missing original collection edge")
            edge = edges[consumed]
            consumed += 1
            if (
                type(edge) is not list
                or len(edge) != 2 + (kind == "dict") + (mode is not None)
                or (kind == "dict" and edge[2] != "dict")
                or (mode is not None and edge[-1] != mode)
                or type(edge[0]) is not int
                or edge[0] != node
                or type(edge[1]) is not int
                or edge[1] < 0
            ):
                raise ValueError("invalid original collection edge")
            if type(descriptor) is dict and descriptor.get("namespace") == "verified-base":
                from scripts.execution_capacity.original_base_refs import resolve as resolve_base

                logical = ("record", record["sequence"], node) if mode is not None else None
                return resolve_base(self, descriptor, edge[1], mode, kind, scope, logical)
            saved = self.index.find("collection-meta", "ordinal", str(edge[1]))
            if saved is None:
                raise ValueError("complete original collection required")
            metadata = strict_json(saved)
            if metadata["body"] != descriptor:
                raise ValueError("original collection body differs")
            parent = metadata["parent"]
            if record["family"] == "collections" and record["kind"] == "note":
                begin = self.index.find("begin:collections", "ordinal", str(record["ordinal"]))
                if begin is None:
                    raise ValueError("original collection parent missing")
                enclosing = self._record_value(strict_json(begin))
                if enclosing["family"] != "cleanup" and (
                    enclosing["family"],
                    enclosing["ordinal"],
                ) != (parent["family"], parent["ordinal"]):
                    raise ValueError("foreign original collection parent")
            elif record["family"] != "cleanup" and (
                record["kind"] != "end"
                or record["family"] != parent["family"]
                or record["ordinal"] != parent["ordinal"]
            ):
                raise ValueError("foreign original collection parent")
            if kind == "dict":
                from scripts.execution_capacity.original_dictionaries import (
                    DictionaryRows,
                    PlainDictionaryRows,
                )

                cursor = DictionaryRows if mode is None else PlainDictionaryRows
            else:
                cursor = CollectionRows if mode is None else PlainCollectionRows
            logical = ("record", record["sequence"], node) if mode is not None else None
            result = self._finite_cursor(
                cursor, edge[1], metadata["count"], descriptor, scope, logical
            )
            return result, metadata["height"]

        if not _discarded_leaf:
            result = self._body(record["body"], resolve=resolve, stats=stats)
            if consumed != len(edges):
                raise ValueError("extra original collection edge")
            return result
        # Only _parts' discarded, edge-free note verification uses this path.
        # A malformed body declaring a collection still calls the same resolve
        # callback and fails at the original missing-edge predicate. No cursor
        # can be registered or returned from a genuinely edge-free body.
        raw = self._body_raw(record["body"])
        lease = self.budget.reserve_workspace(len(raw) * 64, largest=len(raw))
        result = None
        try:
            result = _decode(raw, self.budget, resolve=resolve, stats=stats, _borrowed_prepaid=True)
            if consumed != len(edges):
                raise ValueError("extra original collection edge")
        except BaseException:
            lease.promote()
            raise
        else:
            result = None
            raw = None
            lease.release()

    @_serialized
    def import_native(self, view):
        from scripts.execution_capacity.original_imports import import_body

        return import_body(self, view)

    @_serialized
    def bind_verified_base(self, base):
        from scripts.execution_capacity.original_base_refs import bind

        return bind(self, base)

    @_serialized
    def reference_base_collection(self, rows):
        self._usable(writing=True)
        from scripts.execution_capacity.original_base_refs import reference

        return reference(self, rows)

    @_serialized
    def reference_base_graph(self, value):
        self._usable(writing=True)
        from scripts.execution_capacity.original_base_refs import reference_graph

        return reference_graph(self, value)

    def _complete(self):
        if len(self.native_bodies) != self.index.count("begin:native-imports"):
            raise ValueError("native import closure differs")
        for family in FAMILIES | {"collections", "native-imports"}:
            if self.index.count("begin:" + family) != self.index.count("end:" + family):
                raise ValueError("pending original occurrences")

    @_serialized
    def seal(self, binding, *, original_roots=False):
        self._usable(writing=True)
        self._complete()
        if type(original_roots) is not bool or (
            original_roots and self.index.count("begin:cleanup") != 1
        ):
            raise ValueError("unique original cleanup root required")
        manifest_linked, manifest_identity = False, None
        try:
            if self._base_evidence is not None:
                from scripts.execution_capacity.original_base_refs import binding as base_binding

                with self._base_evidence.base.open_evidence(budget=self.budget) as fresh_base:
                    if encode(base_binding(fresh_base)) != encode(self.verified_base):
                        raise ValueError("verified base changed before seal")
            # Imported directories remain raw authority through the final marker.
            # Freshly reopen them: a successful earlier copy is not a seal.
            from scripts.execution_capacity.original_imports import verify_import

            for kind in ("begin", "end"):
                for raw in self.index.rows(kind + ":native-imports"):
                    verify_import(self, strict_json(raw))
            # The manifest is the final durability marker; it never precedes raw fsync.
            self.body_segments.verify(self.body_bytes, self.body_hash.hexdigest())
            self.log_segments.verify(self.log_bytes, self.log_hash.hexdigest())
            self.body_segments.sync()
            self.log_segments.sync()
            manifest = {
                "schema": 2,
                "encoding": 2,
                "native_bodies": self.native_bodies,
                "verified_base": self.verified_base,
                "chunk_bytes": self.chunk_bytes,
                "body_chunks": self.body_segments.chunks,
                "log_chunks": self.log_segments.chunks,
                "state": "complete",
                "binding": binding,
                "records": self.records,
                "body_bytes": self.body_bytes,
                "body_sha256": self.body_hash.hexdigest(),
                "log_bytes": self.log_bytes,
                "log_sha256": self.log_hash.hexdigest(),
                "original_roots": original_roots,
                "families": {
                    family: self.index.count("begin:" + family) for family in sorted(FAMILIES)
                },
            }
            self.budget.charge(manifest)
            raw = encode(manifest)
            self.budget.reserve(len(raw), rows=0, largest=len(raw))
            fd = self._file("manifest.pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                _write(fd, raw)
                os.fsync(fd)
                self._check_file("manifest.pending", fd)
                os.fsync(self.directory)
                os.fsync(self.parent)
                # Hardlink commits only into a previously absent name.
                os.link(
                    "manifest.pending",
                    "manifest.json",
                    src_dir_fd=self.directory,
                    dst_dir_fd=self.directory,
                    follow_symlinks=False,
                )
                manifest_linked, manifest_identity = True, _identity(os.fstat(fd))
                os.unlink("manifest.pending", dir_fd=self.directory)
                os.fsync(self.directory)
            finally:
                os.close(fd)
            self.binding, self.sealed = binding, True
            self.manifest = manifest
            return manifest
        except BaseException:
            self.valid = False
            # A manifest whose final fsync failed cannot be consumed as complete.
            with contextlib.suppress(OSError, ValueError):
                self._check()
                if (
                    manifest_linked
                    and _identity(
                        os.stat("manifest.json", dir_fd=self.directory, follow_symlinks=False)
                    )
                    == manifest_identity
                ):
                    os.unlink("manifest.json", dir_fd=self.directory)
            raise

    def _rebuild_line(self, line, offset, parsed_hash):
        # The caller owns the bounded parse workspace until this frame returns.
        parsed_hash.update(line)
        row = strict_json(line)
        if (
            type(row) is not dict
            or set(row)
            not in (
                {"sequence", "kind", "family", "ordinal", "body"},
                {"sequence", "kind", "family", "ordinal", "body", "edges"},
            )
            or type(row["sequence"]) is not int
            or row["sequence"] != self.records
            or row["family"] not in FAMILIES | {"collections", "native-imports"}
            or row["kind"] not in ("begin", "note", "end")
            or type(row["ordinal"]) is not int
            or row["ordinal"] < 0
            or type(row["body"]) is not dict
            or set(row["body"]) != {"namespace", "encoding", "offset", "bytes", "sha256"}
        ):
            raise ValueError("invalid original journal occurrence")
        if row["family"] == "collections" and row["kind"] == "end":
            from scripts.execution_capacity.original_collections import validate_collection

            validate_collection(self, row["ordinal"], row["body"])
        else:
            self._record_value(row)
        if row["family"] == "native-imports":
            from scripts.execution_capacity.original_imports import verify_import

            verify_import(self, row)
        if row["family"] == "collections" and row["kind"] == "begin":
            self._validate_collection_parent_body(row["body"])
        if row["body"]["offset"] == offset:
            self._remember_body(row["body"])
            offset += row["body"]["bytes"]
        else:
            saved = self.index.find("bodies", "offset", str(row["body"]["offset"]))
            if saved is None or strict_json(saved) != row["body"]:
                raise ValueError("unclosed original body reference")
        stream = row["kind"] + ":" + row["family"]
        if row["kind"] == "begin":
            if row["ordinal"] != self.index.count(stream):
                raise ValueError("original begin order differs")
        elif self.index.find("begin:" + row["family"], "ordinal", str(row["ordinal"])) is None:
            raise ValueError("unowned original completion")
        if (
            row["kind"] == "note"
            and self.index.find("end:" + row["family"], "ordinal", str(row["ordinal"])) is not None
        ):
            raise ValueError("original note follows completion")
        self._index_row(row)
        self.records += 1
        return offset

    def _rebuild(self):
        fd = self._file("manifest.json", os.O_RDONLY)
        try:
            before = os.fstat(fd)
            if before.st_size > self.budget.row_limit:
                raise EvidenceQuotaError("original manifest quota exceeded")
            self.budget.reserve(before.st_size * 64, rows=1)
            raw = os.pread(fd, before.st_size, 0)
            manifest = strict_json(raw)
            self._check_file("manifest.json", fd)
            if _stamp(before) != _stamp(os.fstat(fd)):
                raise ValueError("original manifest changed")
        finally:
            os.close(fd)
        if (
            type(manifest) is not dict
            or set(manifest)
            != {
                "schema",
                "encoding",
                "native_bodies",
                "verified_base",
                "chunk_bytes",
                "body_chunks",
                "log_chunks",
                "state",
                "binding",
                "records",
                "body_bytes",
                "body_sha256",
                "log_bytes",
                "log_sha256",
                "original_roots",
                "families",
            }
            or type(manifest["schema"]) is not int
            or manifest["schema"] != 2
            or type(manifest["encoding"]) is not int
            or manifest["encoding"] != 2
            or manifest["state"] != "complete"
            or type(manifest["original_roots"]) is not bool
            or type(manifest["families"]) is not dict
            or set(manifest["families"]) != FAMILIES
            or any(type(n) is not int or n < 0 for n in manifest["families"].values())
            or (manifest["original_roots"] and manifest["families"]["cleanup"] != 1)
        ):
            raise ValueError("invalid original journal manifest")
        if encode(manifest["verified_base"]) != encode(self.verified_base):
            raise ValueError("missing or different verified base namespace")
        for key in ("records", "body_bytes", "log_bytes"):
            if type(manifest[key]) is not int or manifest[key] < 0:
                raise ValueError("invalid original journal count")
        from scripts.execution_capacity.original_imports import validate_entry

        if type(manifest["native_bodies"]) is not list:
            raise ValueError("closed native import namespace inventory required")
        for ordinal, entry in enumerate(manifest["native_bodies"]):
            self.budget.charge(entry)
            validate_entry(entry, ordinal)
        self.native_bodies = manifest["native_bodies"]
        self.chunk_bytes = manifest["chunk_bytes"]
        self.body_segments = OriginalSegments(
            self,
            "body",
            chunk_bytes=self.chunk_bytes,
            creating=False,
            descriptors=manifest["body_chunks"],
        )
        self.log_segments = OriginalSegments(
            self,
            "log",
            chunk_bytes=self.chunk_bytes,
            creating=False,
            descriptors=manifest["log_chunks"],
        )
        expected = {"manifest.json"} | {
            f"native-{ordinal:06}" for ordinal in range(len(self.native_bodies))
        }
        for segments in (self.body_segments, self.log_segments):
            for descriptor in segments.chunks:
                expected.add(segments.name(descriptor["ordinal"]))
        count = 0
        with os.scandir(self.directory) as entries:
            for entry in entries:
                self.budget.reserve(128, rows=1)
                if entry.name not in expected:
                    raise ValueError("original journal membership differs")
                count += 1
        if count != len(expected):
            raise ValueError("original journal membership differs")
        self.body_segments.verify(manifest["body_bytes"], manifest["body_sha256"])
        self.log_segments.verify(manifest["log_bytes"], manifest["log_sha256"])
        offset, parsed_hash = 0, sha256()
        with contextlib.closing(self.log_segments.lines(self.budget.row_limit)) as lines:
            for line in lines:
                if len(line) > self.budget.row_limit or not line.endswith(b"\n"):
                    raise ValueError("invalid original journal frame")
                # The index quota owns the encoded row; only this frame's parse
                # graph and validation workspace can end after the last index write.
                self.budget.reserve(128, rows=1)
                workspace = self.budget.reserve_workspace(len(line) * 64)
                try:
                    offset = self._rebuild_line(line, offset, parsed_hash)
                except BaseException:
                    workspace.promote()
                    line = None
                    raise
                else:
                    line = None
                    workspace.release()
        if (
            self.records != manifest["records"]
            or offset != manifest["body_bytes"]
            or parsed_hash.hexdigest() != manifest["log_sha256"]
        ):
            raise ValueError("original journal closure differs")
        self._complete()
        if manifest["families"] != {
            family: self.index.count("begin:" + family) for family in FAMILIES
        }:
            raise ValueError("original family closure differs")
        self.binding, self.sealed = manifest["binding"], True
        self.manifest = manifest

    def __enter__(self):
        self._usable()
        return self

    def __exit__(self, kind, value, traceback):
        self.close()

    @_serialized
    def close(self):
        if self.closed:
            return
        try:
            with contextlib.ExitStack() as closing:
                for fd in (self.parent, self.directory):
                    if fd is not None:
                        closing.callback(os.close, fd)
                for segments in (self.body_segments, self.log_segments):
                    if segments is not None:
                        closing.callback(segments.close)
                if self._base_evidence is not None:
                    closing.callback(self._base_evidence.close)
                if self.index is not None:
                    closing.callback(self.index.close)
        finally:
            # A suspended cursor is closed by CapacityIndex.close before its
            # owner quota returns. If index close itself cannot establish a
            # closed state, retain the lease conservatively.
            if self._index_workspace is not None and (self.index is None or self.index._closed):
                self._index_workspace.release()
                self._index_workspace = None
            self.closed = True
