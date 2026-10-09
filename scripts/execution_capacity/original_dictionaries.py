"""Concrete finite original dictionary producers; no generic Mapping authority."""

from collections.abc import Mapping
from dataclasses import dataclass

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.original_collections import CollectionWriter


def key_identity(owner, key):
    if type(key) is not str:
        raise ValueError("exact original dictionary string key required")
    owner.budget.reserve(len(key) * 16 + 128, rows=0, largest=len(key) * 8 + 128)
    # Fixed-width codepoint hex has exactly Python str lexicographic order,
    # including NUL/prefix/lone surrogate keys accepted by the old JSON codec.
    return ":" + key.encode("utf-32-be", "surrogatepass").hex()


def remember_entry(owner, ordinal, key, position):
    identity = key_identity(owner, key)
    stream = "dictionary-keys:" + str(ordinal)
    saved = owner.index.find(stream, "key", identity)
    owner.budget.reserve(len(key) * 12 + 256, rows=1, largest=len(key) * 12 + 256)
    raw = encode([key, position])
    if saved is None:
        owner.index.append(stream, raw, keys={"key": identity})
    elif saved != raw:
        raise ValueError("duplicate original dictionary key")


@dataclass(frozen=True, eq=False)
class DictionaryRows(Mapping):
    owner: object
    producer_ordinal: int
    length: int
    body_descriptor: dict
    scope: object = None
    logical_node: object = None

    def __len__(self):
        self.owner._collection_metadata(self)
        return self.length

    def _entry(self, ordinal):
        self.owner._collection_metadata(self)
        raw = self.owner.index.find(
            "note:collections:" + str(self.producer_ordinal), "ordinal", str(ordinal)
        )
        if raw is None:
            raise ValueError("original dictionary entry missing")
        scope = (
            self.scope
            if self.scope is not None
            else self.owner._root_scope(
                ("producer", self.body_descriptor["namespace"], self.producer_ordinal)
            )
        )
        value = self.owner._record_value(strict_json(raw), scope=scope)
        if type(value) is not list or len(value) != 2 or type(value[0]) is not str:
            raise ValueError("original dictionary entry differs")
        return value

    def __getitem__(self, key):
        with self.owner._lock:
            self.owner._collection_metadata(self)
            identity = key_identity(self.owner, key)
            raw = self.owner.index.find(
                "dictionary-keys:" + str(self.producer_ordinal), "key", identity
            )
            if raw is None:
                raise KeyError(key)
            actual, ordinal = strict_json(raw)
            if actual != key or type(ordinal) is not int or not 0 <= ordinal < self.length:
                raise ValueError("original dictionary key index differs")
            found, value = self._entry(ordinal)
            if found != key:
                raise ValueError("original dictionary key body differs")
            return value

    def __iter__(self):
        size = len(self)
        for ordinal in range(size):
            yield self._entry(ordinal)[0]
        if len(self) != size:
            raise ValueError("original dictionary snapshot changed")

    def sorted_keys(self):
        size, count = len(self), 0
        for raw in self.owner.index.identity_rows(
            "dictionary-keys:" + str(self.producer_ordinal), "key"
        ):
            self.owner._collection_metadata(self)
            key, ordinal = strict_json(raw)
            if self._entry(ordinal)[0] != key:
                raise ValueError("original dictionary key body differs")
            count += 1
            yield key
        if count != size or len(self) != size:
            raise ValueError("original dictionary sorted coverage differs")

    def __eq__(self, other):
        raise TypeError("explicit complete original dictionary comparison required")


@dataclass(frozen=True, eq=False)
class PlainDictionaryRows(DictionaryRows):
    def __post_init__(self):
        if self.logical_node is None:
            self.owner._register_plain_node(self)

    def __getitem__(self, key):
        from scripts.execution_capacity.original_plain import plain_graph

        return plain_graph(super().__getitem__(key), owner=self.owner, budget=self.owner.budget)

    def __iter__(self):
        return self.sorted_keys()


class DictionaryWriter(CollectionWriter):
    def append(self, key, value):
        owner = self.owner
        with owner._lock:
            owner._usable(writing=True)
            identity = key_identity(owner, key)
            if (
                owner.index.find("dictionary-keys:" + str(self.token.ordinal), "key", identity)
                is not None
            ):
                owner.valid = False
                raise ValueError("duplicate original dictionary key")
            position = owner.index.count("note:collections:" + str(self.token.ordinal))
            owner.budget.reserve(32, rows=1, largest=32)
            owner.note(self.token, [key, value])
            remember_entry(owner, self.token.ordinal, key, position)


@dataclass(frozen=True, eq=False)
class BaseDictionaryRows(DictionaryRows):
    def __getitem__(self, key):
        from scripts.execution_capacity.original_base_refs import rehome_graph, source_rows

        self.owner._collection_metadata(self)
        scope = (
            self.scope
            if self.scope is not None
            else self.owner._root_scope(("producer", "verified-base", self.producer_ordinal))
        )
        return rehome_graph(self.owner, source_rows(self.owner, self)[key], scope=scope)

    def __iter__(self):
        from scripts.execution_capacity.original_base_refs import source_rows

        size = len(self)
        yield from source_rows(self.owner, self)
        if len(self) != size:
            raise ValueError("original base dictionary snapshot changed")

    def sorted_keys(self):
        from scripts.execution_capacity.original_base_refs import source_rows

        size = len(self)
        yield from source_rows(self.owner, self).sorted_keys()
        if len(self) != size:
            raise ValueError("original base dictionary snapshot changed")


@dataclass(frozen=True, eq=False)
class PlainBaseDictionaryRows(BaseDictionaryRows):
    def __post_init__(self):
        if self.logical_node is None:
            self.owner._register_plain_node(self)
