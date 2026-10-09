"""Closed collection producers whose raw rows precede completion authority.

Rows are bounded graphs retained durably as producer notes. Completion writes an
ordered whole-body stream into fixed candidate segments, then compares all bytes
before sharing. Disposable metadata never replaces the original producer log.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.original_segments import OriginalSegments

BUFFER = 64 * 1024


@dataclass(frozen=True)
class CollectionRows(Sequence):
    owner: object
    producer_ordinal: int
    length: int
    body_descriptor: dict
    scope: object = None
    logical_node: object = None

    def __len__(self):
        with self.owner._lock:
            self.owner._collection_metadata(self)
            return self.length

    def __getitem__(self, ordinal):
        if type(ordinal) is not int:
            raise TypeError("finite collection ordinal required")
        with self.owner._lock:
            self.owner._collection_metadata(self)
            if ordinal < 0:
                ordinal += self.length
            if not 0 <= ordinal < self.length:
                raise IndexError(ordinal)
            raw = self.owner.index.find(
                "note:collections:" + str(self.producer_ordinal), "ordinal", str(ordinal)
            )
            if raw is None:
                raise ValueError("missing original collection row")
            scope = (
                self.scope
                if self.scope is not None
                else self.owner._root_scope(
                    ("producer", self.body_descriptor["namespace"], self.producer_ordinal)
                )
            )
            return self.owner._record_value(strict_json(raw), scope=scope)


@dataclass(frozen=True)
class PlainCollectionRows(CollectionRows):
    """Only the historical JSON plain conversion, with original raw provenance."""

    def __post_init__(self):
        if self.logical_node is None:
            self.owner._register_plain_node(self)

    def __getitem__(self, ordinal):
        from scripts.execution_capacity.original_plain import plain_graph

        return plain_graph(super().__getitem__(ordinal), owner=self.owner, budget=self.owner.budget)


@dataclass(frozen=True)
class BaseCollectionRows(CollectionRows):
    """Finite rows bound to the target owner's independently verified base."""

    def __getitem__(self, ordinal):
        if type(ordinal) is not int:
            raise TypeError("finite base collection ordinal required")
        with self.owner._lock:
            self.owner._collection_metadata(self)
            from scripts.execution_capacity.original_base_refs import rehome_graph, source_rows

            scope = (
                self.scope
                if self.scope is not None
                else self.owner._root_scope(("producer", "verified-base", self.producer_ordinal))
            )
            return rehome_graph(self.owner, source_rows(self.owner, self)[ordinal], scope=scope)


@dataclass(frozen=True)
class PlainBaseCollectionRows(BaseCollectionRows):
    def __post_init__(self):
        if self.logical_node is None:
            self.owner._register_plain_node(self)

    def __getitem__(self, ordinal):
        # Source cursor applies the exact closed plain-json-v1 conversion.
        return super().__getitem__(ordinal)


class CollectionWriter:
    def __init__(self, owner, token):
        self.owner, self.token = owner, token

    def append(self, value):
        self.owner.note(self.token, value)

    def complete(self):
        owner, ordinal = self.owner, self.token.ordinal
        with owner._lock:
            owner._usable(writing=True)
            owner.ordinal(self.token, family="collections")
            if owner.index.find("end:collections", "ordinal", str(ordinal)) is not None:
                raise ValueError("collection already completed")
            try:
                descriptor = store_collection(owner, ordinal)
                metadata = validate_collection(owner, ordinal, descriptor)
                owner._commit_record("end", "collections", ordinal, descriptor)
                if metadata.get("kind") == "dict":
                    from scripts.execution_capacity.original_dictionaries import DictionaryRows

                    return DictionaryRows(owner, ordinal, metadata["count"], descriptor)
                return CollectionRows(owner, ordinal, metadata["count"], descriptor)
            except BaseException:
                owner.valid = False
                raise


def _parts(owner, ordinal):
    stream = "note:collections:" + str(ordinal)
    count = owner.index.count(stream)
    begin = owner.index.find("begin:collections", "ordinal", str(ordinal))
    if begin is None:
        raise ValueError("original collection begin missing")
    parent = owner._record_value(strict_json(begin))
    kind = "dictionary" if parent.get("kind") == "dict" else "collection"
    yield encode([kind, 2, count]) + b"\n"
    for position, row in enumerate(owner.index.rows(stream)):
        record = strict_json(row)
        descriptor = record["body"]
        # Decode every exact typed original, including empty rows and aliases.
        if type(record.get("edges", [])) is list and record.get("edges", []) == []:
            owner._record_value(record, _discarded_leaf=True)
        else:
            owner._record_value(record)
        yield b"[" + str(position).encode() + b","
        for offset in range(0, descriptor["bytes"], BUFFER):
            yield owner.body_segments.read(
                min(BUFFER, descriptor["bytes"] - offset), descriptor["offset"] + offset
            )
        yield b"]\n"
    if owner.index.count(stream) != count:
        raise ValueError("original collection grew during snapshot")


def store_collection(owner, ordinal):
    candidate = OriginalSegments(owner, "candidate", chunk_bytes=owner.chunk_bytes, creating=True)
    hashed, complete = sha256(), False
    try:
        for part in _parts(owner, ordinal):
            owner.budget.reserve(len(part) * 2, rows=0)
            candidate.write(part)
            hashed.update(part)
        candidate.sync()
        digest, size = hashed.hexdigest(), candidate.length
        candidate.verify(size, digest)
        saved = owner.index.find("bodies", "digest", owner._lookup_key(digest))
        if saved is not None:
            saved = strict_json(saved)
            if saved["bytes"] == size:
                match, existing_hash = True, sha256()
                for offset in range(0, size, BUFFER):
                    length = min(BUFFER, size - offset)
                    existing = owner.body_segments.read(length, saved["offset"] + offset)
                    existing_hash.update(existing)
                    if existing != candidate.read(length, offset):
                        match = False
                if existing_hash.hexdigest() != saved["sha256"]:
                    raise ValueError("original collection candidate changed")
                if match:
                    complete = True
                    return saved
        descriptor = {
            "namespace": "local",
            "encoding": 2,
            "offset": owner.body_bytes,
            "bytes": size,
            "sha256": digest,
        }
        copied = sha256()
        for offset in range(0, size, BUFFER):
            part = candidate.read(min(BUFFER, size - offset), offset)
            owner.body_segments.write(part)
            owner.body_bytes += len(part)
            owner.body_hash.update(part)
            copied.update(part)
        owner.body_segments.sync()
        candidate.verify(size, digest)
        if copied.hexdigest() != digest:
            raise ValueError("original collection copy changed")
        owner._remember_body(descriptor)
        complete = True
        return descriptor
    finally:
        try:
            if complete:
                for chunk in candidate.chunks:
                    candidate._select(chunk["ordinal"])
                    candidate._check()
                    os.unlink(candidate.name(chunk["ordinal"]), dir_fd=owner.directory)
                os.fsync(owner.directory)
        finally:
            candidate.close()


def validate_collection(owner, ordinal, descriptor):
    if (
        type(descriptor) is not dict
        or set(descriptor) != {"namespace", "encoding", "offset", "bytes", "sha256"}
        or descriptor["namespace"] != "local"
        or type(descriptor["encoding"]) is not int
        or descriptor["encoding"] != 2
        or type(descriptor["offset"]) is not int
        or type(descriptor["bytes"]) is not int
        or descriptor["offset"] < 0
        or descriptor["bytes"] <= 0
        or descriptor["offset"] + descriptor["bytes"] > owner.body_segments.length
    ):
        raise ValueError("closed original collection body required")
    begin = owner.index.find("begin:collections", "ordinal", str(ordinal))
    if begin is None:
        raise ValueError("unowned original collection completion")
    parent = owner._record_value(strict_json(begin))
    offset, hashed = 0, sha256()
    for part in _parts(owner, ordinal):
        if offset + len(part) > descriptor["bytes"]:
            raise ValueError("original collection rows differ")
        actual = owner.body_segments.read(len(part), descriptor["offset"] + offset)
        if actual != part:
            raise ValueError("original collection row order or typed body differs")
        hashed.update(actual)
        offset += len(part)
    if offset != descriptor["bytes"] or hashed.hexdigest() != descriptor["sha256"]:
        raise ValueError("original collection closure differs")
    height, nodes = 0, 1
    stream = "note:collections:" + str(ordinal)
    dictionary = parent.get("kind") == "dict"
    for position, raw in enumerate(owner.index.rows(stream)):
        stats = {}
        value = owner._record_value(strict_json(raw), stats=stats)
        if dictionary:
            from scripts.execution_capacity.original_dictionaries import remember_entry

            if type(value) is not list or len(value) != 2 or type(value[0]) is not str:
                raise ValueError("original dictionary entry differs")
            remember_entry(owner, ordinal, value[0], position)
        height = max(height, stats["height"] + (0 if dictionary else 1))
        nodes += stats["nodes"] - (2 if dictionary else 0)
    if dictionary and owner.index.count("dictionary-keys:" + str(ordinal)) != owner.index.count(
        stream
    ):
        raise ValueError("original dictionary key coverage differs")
    if height > 64:
        raise ValueError("original collection nesting exceeds bound")
    metadata = {
        "body": descriptor,
        "count": owner.index.count(stream),
        "parent": parent,
        "height": height,
        "nodes": nodes,
    }
    if dictionary:
        metadata["kind"] = "dict"
    owner.index.append("collection-meta", encode(metadata), keys={"ordinal": str(ordinal)})
    return metadata
