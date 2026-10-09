"""Fixed derived predicate state, rebuilt from every verified original read.

These maps are disposable computation state. They cannot be encoded as original
evidence or used as a replacement for reading the original journals.
"""

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.original_dictionaries import key_identity
from scripts.execution_capacity.original_journal import OriginalJournal, _decode, _graph_parts
from scripts.execution_capacity.replay_relations import same_value

_FAMILIES = {
    "current",
    "actual_leases",
    "previous_batches",
    "states",
    "observations",
    "leases",
    "final_reads",
    "seen_current",
    "lease_ids",
    "attempts",
    "source_membership",
    "source_parents",
    "source_batches",
    "source_batch_parents",
    "source_live_parents",
    "source_origins",
    "source_admitted",
    "source_expected",
    "source_actual",
    "source_scopes",
}


class PredicateMap:
    def __init__(self, owner, family):
        if type(owner) is not OriginalJournal or family not in _FAMILIES:
            raise ValueError("actual owner and fixed predicate family required")
        owner._usable()
        self.owner = owner
        owner.budget.reserve(256, rows=1, largest=256)
        session = owner.index.append("predicate-map-sessions", b"{}")
        self.prefix = "predicate-map:" + str(session) + ":" + family

    def __setitem__(self, key, value):
        identity = key_identity(self.owner, key)
        raw = bytearray()
        # Derived entries are one closed row. Finite original graph references
        # have no resolver here and cannot silently become scratch authority.
        for part in _graph_parts(value, self.owner.budget):
            self.owner.budget.reserve(len(part) * 2, rows=0, largest=len(raw) + len(part))
            raw.extend(part)
        self.owner.budget.reserve(
            len(raw) * 2 + len(identity) + 256, rows=1, largest=len(raw) + len(identity) + 256
        )
        if self.owner.index.find(self.prefix, "identity", identity) is None:
            self.owner.index.append(self.prefix, encode(key), keys={"identity": identity})
        self.owner.index.group(self.prefix, identity, bytes(raw))

    def __contains__(self, key):
        self.owner._usable()
        return (
            self.owner.index.find(self.prefix, "identity", key_identity(self.owner, key))
            is not None
        )

    def get(self, key, default=None):
        self.owner._usable()
        raw = self.owner.index.group_last(self.prefix, key_identity(self.owner, key))
        return default if raw is None else _decode(raw, self.owner.budget)

    def __getitem__(self, key):
        if key not in self:
            raise KeyError(key)
        return self.get(key)

    def __len__(self):
        self.owner._usable()
        return self.owner.index.count(self.prefix)

    def __iter__(self):
        self.owner._usable()
        size, count = len(self), 0
        for raw in self.owner.index.rows(self.prefix):
            self.owner.budget.reserve(len(raw) * 8 + 128, rows=1, largest=len(raw) * 8 + 128)
            key = strict_json(raw)
            if key not in self:
                raise ValueError("derived predicate identity differs")
            count += 1
            yield key
        if count != size or len(self) != size:
            raise ValueError("derived predicate coverage differs")

    def items(self):
        for key in self:
            yield key, self[key]

    def sorted_items(self):
        self.owner._usable()
        size, count = len(self), 0
        for raw in self.owner.index.identity_rows(self.prefix, "identity"):
            self.owner.budget.reserve(len(raw) * 8 + 128, rows=1, largest=len(raw) * 8 + 128)
            key = strict_json(raw)
            count += 1
            yield key, self[key]
        if count != size or len(self) != size:
            raise ValueError("derived predicate sorted coverage differs")

    def values(self):
        for key in self:
            yield self[key]

    def _other(self, other):
        from scripts.execution_capacity.original_dictionaries import (
            BaseDictionaryRows,
            DictionaryRows,
            PlainBaseDictionaryRows,
            PlainDictionaryRows,
        )

        self.owner._usable()
        if type(other) in (
            DictionaryRows,
            PlainDictionaryRows,
            BaseDictionaryRows,
            PlainBaseDictionaryRows,
        ):
            if other.owner is not self.owner:
                raise ValueError("predicate comparison owner differs")
            self.owner._collection_metadata(other)
        elif type(other) is PredicateMap:
            if other.owner is not self.owner:
                raise ValueError("predicate comparison owner differs")
            other.owner._usable()
        elif type(other) is not dict:
            raise ValueError("closed predicate comparison required")

    def same_keys(self, other):
        self._other(other)
        equal = len(self) == len(other)
        for key in self:
            if key not in other:
                equal = False
        return equal

    def same_values(self, other):
        self._other(other)
        equal = len(self) == len(other)
        for key, value in self.items():
            if key not in other or not same_value(
                value, other[key], owner=self.owner, budget=self.owner.budget
            ):
                equal = False
        return equal
