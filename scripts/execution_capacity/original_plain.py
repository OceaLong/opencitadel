"""Exact canonical plain JSON over explicit owned finite collections."""

import json
from hashlib import sha256

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


def canonical_parts(value, *, owner, budget):
    active = set()

    def visit(item, depth=0):
        if depth > 64:
            raise ValueError("native original JSON nesting exceeds bound")
        budget.reserve(128, rows=1)
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
            if owner is None:
                raise ValueError("explicit native original collection owner required")
            owner._collection_metadata(item)
        if type(item) in (dict, list, tuple) or finite:
            if id(item) in active:
                raise ValueError("cyclic native original JSON")
            active.add(id(item))
            if type(item) is dict or dictionary:
                if any(type(key) is not str for key in item):
                    raise ValueError("native original JSON keys must be strings")
                if not dictionary:
                    budget.reserve(len(item) * 16, rows=0)
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
                yield b"["
                for ordinal in range(size):
                    if ordinal:
                        yield b","
                    yield from visit(item[ordinal], depth + 1)
                if finite and len(item) != size:
                    raise ValueError("native original collection snapshot changed")
                yield b"]"
            active.remove(id(item))
        elif item is None or type(item) in (str, int, bool, float):
            if type(item) is str:
                budget.reserve(len(item) * 12 + 2, rows=0, largest=len(item) * 12 + 2)
            raw = json.dumps(item, separators=(",", ":"), allow_nan=False).encode()
            budget.reserve(len(raw), rows=0, largest=len(raw))
            yield raw
        else:
            raise ValueError("closed native original plain JSON required")

    yield from visit(value)


def canonical_original_digest(value, *, owner, budget):
    hashed = sha256()
    for part in canonical_parts(value, owner=owner, budget=budget):
        hashed.update(part)
    return hashed.hexdigest()


def copy_plain_graph(value, *, source, target, parent):
    """Transfer exact plain nodes with bounded per-entry roots and disk identity."""
    from scripts.acceptance.capacity_io import strict_json
    from scripts.execution_capacity.attempt import encode

    target.budget.reserve(256, rows=1, largest=256)
    session = target.index.append("plain-copy-sessions", b"{}")
    stream = "plain-copy:" + str(session)

    def copy_root(root):
        held, active = {}, set()

        def copy(item, depth=0):
            if depth > 64:
                raise ValueError("native original graph nesting exceeds bound")
            target.budget.reserve(256, rows=1)
            if len(held) * 256 > target.budget.row_limit:
                raise ValueError("native held root requires bounded collection producers")
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
                source._collection_metadata(item)
            if type(item) in (dict, list, tuple) or finite:
                key = id(item)
                if key in active:
                    raise ValueError("cyclic native original graph")
                if key in held:
                    return held[key][1]
                active.add(key)
                if finite:
                    target.budget.charge(item.body_descriptor)
                    target.budget.reserve(1024, rows=1, largest=1024)
                    identity = encode(
                        [
                            type(item).__name__,
                            item.producer_ordinal,
                            item.logical_node,
                            item.body_descriptor,
                        ]
                    ).decode()
                    saved = target.index.find(stream, "producer", identity)
                    cursor = DictionaryRows if dictionary else CollectionRows
                    if saved is not None:
                        ordinal, count, descriptor = strict_json(saved)
                        result = cursor(target, ordinal, count, descriptor)
                        target._collection_metadata(result)
                    else:
                        slot = "node-" + str(target.index.count("begin:collections"))
                        writer = (
                            target.begin_dictionary(parent, slot)
                            if dictionary
                            else target.begin_collection(parent, slot)
                        )
                        size = len(item)
                        if dictionary:
                            for entry_key in item:
                                writer.append(entry_key, copy_root(item[entry_key]))
                        else:
                            for ordinal in range(size):
                                writer.append(copy_root(item[ordinal]))
                        if len(item) != size:
                            raise ValueError("native source collection changed")
                        result = writer.complete()
                        target.budget.charge(result.body_descriptor)
                        payload = encode(
                            [result.producer_ordinal, result.length, result.body_descriptor]
                        )
                        target.budget.reserve(len(payload) + len(identity.encode()) + 256, rows=1)
                        target.index.append(stream, payload, keys={"producer": identity})
                elif type(item) is dict:
                    result = {key: copy(child, depth + 1) for key, child in item.items()}
                else:
                    result = [copy(child, depth + 1) for child in item]
                active.remove(key)
                held[key] = (item, result)
                return result
            if item is None or type(item) in (str, bool, int, float):
                return item
            raise ValueError("closed native original plain value required")

        return copy(root)

    return copy_root(value)


def plain_graph(value, *, owner, budget):
    """Historical plain conversion for a bounded root containing owned cursors."""
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    active = set()
    nodes = 0

    def convert(item, depth=0):
        nonlocal nodes
        nodes += 1
        budget.reserve(256, rows=1)
        if depth > 64 or nodes * 256 > budget.row_limit:
            raise ValueError("plain held root requires bounded collection producers")
        if type(item) in (
            DictionaryRows,
            PlainDictionaryRows,
            BaseDictionaryRows,
            PlainBaseDictionaryRows,
        ):
            owner._collection_metadata(item)
            kind = (
                PlainBaseDictionaryRows
                if type(item) in (BaseDictionaryRows, PlainBaseDictionaryRows)
                else PlainDictionaryRows
            )
            return kind(owner, item.producer_ordinal, item.length, item.body_descriptor)
        if type(item) in (
            CollectionRows,
            PlainCollectionRows,
            BaseCollectionRows,
            PlainBaseCollectionRows,
        ):
            owner._collection_metadata(item)
            kind = (
                PlainBaseCollectionRows
                if type(item) in (BaseCollectionRows, PlainBaseCollectionRows)
                else PlainCollectionRows
            )
            return kind(owner, item.producer_ordinal, item.length, item.body_descriptor)
        if type(item) in (dict, list, tuple):
            if id(item) in active:
                raise ValueError("cyclic original plain root")
            active.add(id(item))
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError("plain original keys must be strings")
                budget.reserve(len(item) * 16, rows=0)
                result = {key: convert(item[key], depth + 1) for key in sorted(item)}
            else:
                result = [convert(child, depth + 1) for child in item]
            active.remove(id(item))
            return result
        if item is None or type(item) in (
            str,
            bool,
            int,
            float,
            bytes,
            UUID,
            Decimal,
            date,
            datetime,
        ):
            budget.charge(item)
            raw = json.dumps(item, default=str, sort_keys=True)
            budget.reserve(len(raw) * 64, rows=0, largest=len(raw))
            return json.loads(raw)
        raise ValueError("closed original plain value required")

    return convert(value)
