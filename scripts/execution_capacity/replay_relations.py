"""Session-local fixed replay relations over actual owner originals.

Scratch references accelerate lookup only. Every match reloads complete original
values; no digest or cached validity substitutes for an original predicate.
"""

from scripts.execution_capacity.attempt import encode
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
from scripts.execution_capacity.original_journal import OriginalJournal, _decode, _graph_parts

_MAPS = (DictionaryRows, PlainDictionaryRows, BaseDictionaryRows, PlainBaseDictionaryRows)

_FINITE = (CollectionRows, PlainCollectionRows, BaseCollectionRows, PlainBaseCollectionRows)


def same_value(left, right, *, owner, budget, depth=0):
    owner._usable()
    budget.reserve(128, rows=1)
    if depth > 64:
        raise ValueError("original comparison depth exceeds bound")
    for value in (left, right):
        if type(value) in (*_FINITE, *_MAPS):
            if value.owner is not owner:
                raise ValueError("foreign original comparison owner")
            owner._collection_metadata(value)
    if type(left) in (dict, *_MAPS) and type(right) in (dict, *_MAPS):
        size = len(left)
        if size != len(right):
            return False
        equal = True
        count = 0
        for key in left:
            count += 1
            if key not in right or not same_value(
                left[key], right[key], owner=owner, budget=budget, depth=depth + 1
            ):
                equal = False
        owner._usable()
        return equal and count == size == len(left) == len(right)
    if type(left) in (list, tuple, *_FINITE) and type(right) in (list, tuple, *_FINITE):
        size = len(left)
        if size != len(right):
            return False
        equal = True
        for ordinal in range(size):
            if not same_value(
                left[ordinal], right[ordinal], owner=owner, budget=budget, depth=depth + 1
            ):
                equal = False
        owner._usable()
        return equal and len(left) == len(right) == size
    if type(left) is not type(right):
        return False
    # Both operands came from the closed typed original decoder. Reject callers
    # trying to extend that vocabulary rather than invoking arbitrary __eq__.
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    if type(left) not in (str, bytes, bool, int, float, type(None), date, datetime, Decimal, UUID):
        raise ValueError("closed original scalar comparison required")
    return left == right


class ReplayRelations:
    def __init__(self, owner, roots, budget):
        if type(owner) is not OriginalJournal or budget is not owner.budget:
            raise ValueError("actual original replay relation owner required")
        owner._usable()
        self.owner, self.roots, self.budget = owner, roots, budget
        budget.reserve(256, rows=1, largest=256)
        ordinal = owner.index.append("replay-sessions", b"{}")
        self.prefix = "replay:" + str(ordinal) + ":"

    def _key(self, value):
        self.owner._usable()
        if type(value) is str:
            self.budget.reserve(len(value) * 12 + 64, rows=0, largest=len(value) * 12 + 64)
        elif type(value) is not int:
            raise ValueError("exact original relation identity required")
        self.budget.reserve(128, rows=0)
        return encode([type(value).__name__, value]).decode()

    def _find(self, family, key):
        self.owner._usable()
        return self.owner.index.find(self.prefix + family, "identity", key)

    def _insert(self, family, key, raw):
        self.budget.reserve(
            len(key.encode()) + len(raw) + 256, rows=1, largest=max(len(raw), len(key.encode()))
        )
        self.owner.index.append(self.prefix + family, raw, keys={"identity": key})

    def snapshot(self, sql_ordinal, row):
        key = self._key(row["uow"])
        prior = self._find("snapshot", key)
        if prior is None:
            self._insert("snapshot", key, encode(sql_ordinal))
        else:
            from scripts.acceptance.capacity_io import strict_json

            original = self.roots["sql"][strict_json(prior)]
            if self._key(original["uow"]) != key or not same_value(
                original["snapshot"], row["snapshot"], owner=self.owner, budget=self.budget
            ):
                raise ValueError("original UOW snapshot differs")

    def parent(self, kind, identity, operand, pair, value):
        from scripts.acceptance.capacity_io import strict_json

        first, second = self._key(kind), self._key(identity)
        self.budget.reserve(
            (len(first) + len(second)) * 2 + 16, rows=0, largest=(len(first) + len(second)) * 2 + 16
        )
        key = encode([first, second]).decode()
        prior = self._find("parent", key)
        if prior is None:
            self._insert("parent", key, encode([operand, pair]))
            return
        previous_operand, previous_pair = strict_json(prior)
        record = self.roots["operands"]["journal-read"][previous_operand]
        if previous_pair is None:
            old_identity, original = record["key"], record["value"]
        else:
            old_identity, original = record["value"][previous_pair]
        if (
            record["kind"] != kind
            or old_identity != identity
            or not same_value(original, value, owner=self.owner, budget=self.budget)
        ):
            raise ValueError("repeated original parent changed")

    def set_principal(self, scope, value):
        key = self._key(scope)
        # A principal is one bounded typed root, never a whole run population.
        raw = bytearray()
        for part in _graph_parts(value, self.budget, owner=self.owner):
            self.budget.reserve(len(part) * 2, rows=0, largest=len(raw) + len(part))
            raw.extend(part)
        previous = self._find("principal", key)
        if previous is not None:
            if not same_value(
                _decode(previous, self.budget), value, owner=self.owner, budget=self.budget
            ):
                raise ValueError("original principal changed")
            return
        self._insert("principal", key, bytes(raw))

    def principal(self, scope):
        raw = self._find("principal", self._key(scope))
        return None if raw is None else _decode(raw, self.budget)

    def snapshot_count(self):
        self.owner._usable()
        return self.owner.index.count(self.prefix + "snapshot")

    def parent_count(self):
        self.owner._usable()
        return self.owner.index.count(self.prefix + "parent")


def typed_relation_key(value, *, owner, budget):
    """Complete sorted typed key, bounded to one row; never a digest proxy."""
    from scripts.execution_capacity.original_journal import _typed_parts

    owner._usable()

    def parts(item, depth=0):
        budget.reserve(128, rows=1)
        if depth > 64:
            raise ValueError("original relation key depth exceeds bound")
        if type(item) is dict:
            budget.reserve(len(item) * 16, rows=0, largest=len(item) * 16)
            if any(type(key) is not str for key in item):
                raise ValueError("original relation key field type differs")
            yield b'["dict",['
            for ordinal, key in enumerate(sorted(item)):
                if ordinal:
                    yield b","
                yield b"["
                yield from parts(key, depth + 1)
                yield b","
                yield from parts(item[key], depth + 1)
                yield b"]"
            yield b"]]"
        elif type(item) in (list, tuple):
            yield b'["list",['
            for ordinal, child in enumerate(item):
                if ordinal:
                    yield b","
                yield from parts(child, depth + 1)
            yield b"]]"
        else:
            # Finite rows cannot be expanded into a lookup key. The complete
            # first read receipt is individually row-bounded by acquisition.
            yield from _typed_parts(item, budget, depth)

    raw = bytearray()
    for part in parts(value):
        budget.reserve(len(part) * 5, rows=0, largest=len(raw) + len(part))
        raw.extend(part)
    owner._usable()
    return raw.decode()


def legacy_attempt_key(kind, values, *, owner, budget):
    """Only the two original Python-set attempt keys use numeric equality.

    Whole original bodies and receipts retain their exact typed comparisons.
    An integer never passes through float, so adjacent large values stay apart.
    """
    import math

    if kind == "parent" and len(values) == 3:
        prefix, numeric = values[:2], values[2]
        if values[0] == "evaluation_judge":
            return typed_relation_key(values, owner=owner, budget=budget)
        if values[0] != "evaluation_subject":
            raise ValueError("closed original attempt parent kind required")
    elif kind == "run-attempt" and len(values) == 2:
        prefix, numeric = values[:1], values[1]
    else:
        raise ValueError("closed original attempt set key required")
    if type(numeric) in (bool, int):
        numerator, denominator = int(numeric), 1
    elif type(numeric) is float and math.isfinite(numeric):
        numerator, denominator = numeric.as_integer_ratio()
    else:
        raise ValueError("finite original numeric attempt key required")
    return typed_relation_key(
        [*prefix, ["python-number", numerator, denominator]], owner=owner, budget=budget
    )
