"""Private JSON compatibility projection with precharged original operands.

This stream is not a type-preserving encoding: typed OriginalView evidence,
never a JSON marker, supplies bytes and semantic authority.
"""

import base64
import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from uuid import UUID

from scripts.execution_capacity.original_dictionaries import (
    BaseDictionaryRows,
    DictionaryRows,
    PlainBaseDictionaryRows,
    PlainDictionaryRows,
)


def _default(value):
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return {f.name: getattr(value, f.name) for f in fields(value)}
    if hasattr(type(value), "model_fields"):
        return {name: getattr(value, name) for name in type(value).model_fields}
    raise ValueError("unsupported private JSON operand")


def _reserve(value, budget, active):
    from scripts.execution_capacity.original_collections import (
        BaseCollectionRows,
        CollectionRows,
        PlainBaseCollectionRows,
        PlainCollectionRows,
    )

    if type(value) in (
        CollectionRows,
        PlainCollectionRows,
        BaseCollectionRows,
        PlainBaseCollectionRows,
        DictionaryRows,
        PlainDictionaryRows,
        BaseDictionaryRows,
        PlainBaseDictionaryRows,
    ):
        raise ValueError("explicit private JSON original owner required")
    budget.reserve(128, rows=1)
    if isinstance(value, bytes):
        # Private compatibility projection only, never a preserving type codec.
        # Reserve raw+base64 buffer, decoded ASCII, JSON escaping and wrapper
        # before _default performs either base64 or JSON allocations.
        encoded = 4 * ((len(value) + 2) // 3)
        budget.reserve(len(value) + encoded * 16 + 1024, rows=1, largest=encoded + 32)
    elif isinstance(value, str):
        budget.reserve(len(value) * 12 + 2, rows=0, largest=len(value) * 12 + 2)
    elif value is None or isinstance(value, (bool, int, float)):
        return
    elif isinstance(value, (Enum, UUID, Decimal, date, datetime)):
        _reserve(_default(value), budget, active)
    else:
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic private JSON operand")
        active.add(identity)
        if isinstance(value, dict):
            budget.reserve(len(value) * 128, rows=0)
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError("private JSON dictionary keys must be strings")
                _reserve(key, budget, active)
                _reserve(child, budget, active)
        elif isinstance(value, (tuple, list)):
            budget.reserve(len(value) * 16, rows=0)
            for child in value:
                _reserve(child, budget, active)
        elif is_dataclass(value):
            budget.reserve(len(fields(value)) * 128, rows=0)
            for field in fields(value):
                _reserve(field.name, budget, active)
                _reserve(getattr(value, field.name), budget, active)
        elif hasattr(type(value), "model_fields"):
            budget.reserve(len(type(value).model_fields) * 128, rows=0)
            for name in type(value).model_fields:
                _reserve(name, budget, active)
                _reserve(getattr(value, name), budget, active)
        else:
            raise ValueError("unsupported private JSON operand")
        active.remove(identity)


def _owned_chunks(value, *, owner, budget):
    from scripts.execution_capacity.original_collections import (
        BaseCollectionRows,
        CollectionRows,
        PlainBaseCollectionRows,
        PlainCollectionRows,
    )
    from scripts.execution_capacity.original_journal import OriginalJournal

    if type(owner) is not OriginalJournal:
        raise ValueError("actual private JSON original owner required")
    owner._usable()
    active = set()

    def visit(item, depth=0):
        if depth > 64:
            raise ValueError("private JSON nesting exceeds bound")
        dictionary = type(item) in (
            DictionaryRows,
            PlainDictionaryRows,
            BaseDictionaryRows,
            PlainBaseDictionaryRows,
        )
        finite = dictionary or type(item) in (
            CollectionRows,
            PlainCollectionRows,
            BaseCollectionRows,
            PlainBaseCollectionRows,
        )
        if finite:
            owner._collection_metadata(item)
        container = isinstance(item, (dict, list, tuple)) or finite
        if container:
            budget.reserve(128, rows=1)
            identity = id(item)
            if identity in active:
                raise ValueError("cyclic private JSON operand")
            active.add(identity)
            if isinstance(item, dict) or dictionary:
                if not dictionary:
                    budget.reserve(len(item) * 128, rows=0)
                if any(not isinstance(key, str) for key in item):
                    raise TypeError("private JSON dictionary keys must be strings")
                yield b"{"
                keys = item.sorted_keys() if dictionary else sorted(item)
                for ordinal, key in enumerate(keys):
                    if ordinal:
                        yield b","
                    yield from visit(key, depth + 1)
                    yield b":"
                    yield from visit(item[key], depth + 1)
                yield b"}"
            else:
                size = len(item)
                if not finite:
                    budget.reserve(size * 16, rows=0)
                yield b"["
                for ordinal in range(size):
                    if ordinal:
                        yield b","
                    yield from visit(item[ordinal], depth + 1)
                if finite:
                    owner._collection_metadata(item)
                    if len(item) != size:
                        raise ValueError("private JSON original snapshot changed")
                yield b"]"
            active.remove(identity)
        elif item is None or isinstance(item, (str, bool, int, float)):
            _reserve(item, budget, set())
            yield json.dumps(item, separators=(",", ":"), allow_nan=False).encode()
        else:
            # Reserve conversion buffers before constructing compatibility values.
            # Dataclasses/models remain bounded held roots, never cursor adapters.
            if is_dataclass(item):
                budget.reserve(len(fields(item)) * 128, rows=1)
            elif hasattr(type(item), "model_fields"):
                budget.reserve(len(type(item).model_fields) * 128, rows=1)
            else:
                _reserve(item, budget, set())
            if id(item) in active:
                raise ValueError("cyclic private JSON operand")
            active.add(id(item))
            yield from visit(_default(item), depth + 1)
            active.remove(id(item))

    yield from visit(value)
    owner._usable()


def chunks(value, *, budget, owner=None):
    if owner is not None:
        return _owned_chunks(value, owner=owner, budget=budget)
    _reserve(value, budget, set())
    return (
        chunk.encode()
        for chunk in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False, default=_default
        ).iterencode(value)
    )


def json_digest(value, *, budget, owner=None):
    hashed = sha256()
    for raw in chunks(value, budget=budget, owner=owner):
        hashed.update(raw)
    return hashed.hexdigest()


def equal_streams(left, right):
    """Compare complete bytes with two borrowed bounded encoder chunks.

    This is only byte comparison. Callers must construct streams through their
    concrete original owner APIs; equality never supplies original authority.
    No concatenation, growing remainder, or chunk-boundary assumption is used.
    """
    left, right = iter(left), iter(right)
    a = b = memoryview(b"")
    left_done = right_done = False
    while True:
        while not a and not left_done:
            try:
                raw = next(left)
            except StopIteration:
                left_done = True
            else:
                if type(raw) is not bytes:
                    raise ValueError("bounded original byte chunk required")
                a = memoryview(raw)
        while not b and not right_done:
            try:
                raw = next(right)
            except StopIteration:
                right_done = True
            else:
                if type(raw) is not bytes:
                    raise ValueError("bounded original byte chunk required")
                b = memoryview(raw)
        if left_done or right_done:
            return left_done and right_done
        length = min(len(a), len(b))
        if a[:length] != b[:length]:
            return False
        a, b = a[length:], b[length:]
