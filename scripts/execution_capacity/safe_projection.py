"""Deterministic complete safe projection; does not itself authorize success."""

import json
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from scripts.acceptance.capacity_c2c_models import (
    HISTORY_FAMILIES,
    OPERAND_FAMILIES,
    PREDICATE_FAMILIES,
    SETTLEMENT_TABLES,
    SOURCE_FAMILIES,
    Collection,
    CompactCollection,
    JournalCollection,
    SafeUnit,
    SafeUnitV2,
)
from scripts.execution_capacity.original_dictionaries import (
    BaseDictionaryRows,
    DictionaryRows,
    PlainBaseDictionaryRows,
    PlainDictionaryRows,
)
from scripts.execution_capacity.original_journal import _Occurrences
from scripts.execution_capacity.original_shards import OriginalView


def typed_digest(value, *, budget, view=None):
    """Original-value.v1: domain then decimal byte-length/colon-framed tokens.

    Concrete sealed view-owned occurrences emit the identical list/count and
    recursively typed value tokens as an in-memory list, with no digest proxy.
    """
    from scripts.execution_capacity.original_collections import (
        BaseCollectionRows,
        CollectionRows,
        PlainBaseCollectionRows,
        PlainCollectionRows,
    )

    hashed = sha256(b"opencitadel.capacity.original-value.v1\x00")
    active = set()

    def emit(raw):
        budget.reserve(len(raw), rows=0)
        hashed.update(str(len(raw)).encode() + b":" + raw)

    def visit(item, depth=0):
        budget.reserve(128, rows=1)
        if depth > 256:
            raise ValueError("private value nesting exceeds limit")
        occurrence = type(item) is _Occurrences
        dictionary = type(item) in (
            DictionaryRows,
            PlainDictionaryRows,
            BaseDictionaryRows,
            PlainBaseDictionaryRows,
        )
        finite = (
            dictionary
            or occurrence
            or type(item)
            in (CollectionRows, PlainCollectionRows, BaseCollectionRows, PlainBaseCollectionRows)
        )
        if finite:
            if (
                type(view) is not OriginalView
                or getattr(view, "journal", None) is not item.owner
                or not item.owner.sealed
                or (occurrence and item.selection is not None)
            ):
                raise ValueError("sealed original collection owner required")
            item.owner._usable()
            if not occurrence:
                item.owner._collection_metadata(item)
        if isinstance(item, (dict, list, tuple)) or finite:
            if id(item) in active:
                raise ValueError("private value cycle")
            active.add(id(item))
            if isinstance(item, dict) or dictionary:
                if any(type(key) is not str for key in item):
                    raise ValueError("private value key type differs")
                if not dictionary:
                    budget.reserve(len(item) * 16, rows=0)
                emit(b"dict")
                emit(str(len(item)).encode())
                keys = item.sorted_keys() if dictionary else sorted(item)
                for key in keys:
                    visit(key, depth + 1)
                    visit(item[key], depth + 1)
            else:
                emit(b"list")
                size = len(item)
                emit(str(size).encode())
                for ordinal in range(size):
                    visit(item[ordinal], depth + 1)
                if finite:
                    item.owner._usable()
                    if len(item) != size:
                        raise ValueError("original collection iteration differs")
            active.remove(id(item))
        elif isinstance(item, bytes):
            emit(b"bytes")
            emit(item)
        elif isinstance(item, (UUID, Decimal, datetime, date)):
            emit(type(item).__name__.encode())
            emit((item.isoformat() if isinstance(item, (datetime, date)) else str(item)).encode())
        elif item is None or isinstance(item, (str, bool, int, float)):
            emit(b"scalar")
            if isinstance(item, str):
                budget.reserve(len(item) * 12, rows=0)
            emit(
                json.dumps(item, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
            )
        else:
            raise ValueError("unsupported original projection type")

    visit(value)
    return hashed.hexdigest()


def project_unit(roots, view, *, kind, origin_sha256, base_sha256, budget):
    version2 = view.manifest.get("schema") == 2
    if version2:
        if type(view) is not OriginalView or getattr(view, "journal", None) is None:
            raise ValueError("concrete original projection view required")
        view.journal._usable()
        if not view.journal.sealed or not view.manifest["original_roots"]:
            raise ValueError("sealed original projection roots required")
    if (
        set(roots) != {"cleanup", "operands", "objects", "sql", "transports"}
        or set(roots["operands"]) != OPERAND_FAMILIES
    ):
        raise ValueError("complete original projection roots required")
    cleanup = roots["cleanup"]
    final = (cleanup["quiescence"] if kind == "base" else cleanup)["final"]

    def digest(value):
        return typed_digest(value, budget=budget, view=view if version2 else None)

    def collection(rows):
        from scripts.execution_capacity.original_collections import (
            BaseCollectionRows,
            CollectionRows,
            PlainBaseCollectionRows,
            PlainCollectionRows,
        )

        if not isinstance(rows, list) and not (
            version2
            and type(rows)
            in (
                _Occurrences,
                CollectionRows,
                PlainCollectionRows,
                BaseCollectionRows,
                PlainBaseCollectionRows,
            )
        ):
            raise TypeError("original ordered collection required")
        if version2:
            return CompactCollection(count=len(rows), sha256=digest(rows))
        budget.reserve(len(rows) * 256, rows=len(rows))
        return Collection(
            count=len(rows),
            sha256=digest(rows),
            records=[{"ordinal": index, "sha256": digest(row)} for index, row in enumerate(rows)],
        )

    def journal(rows):
        if not isinstance(rows, dict) and not (
            version2
            and type(rows)
            in (DictionaryRows, PlainDictionaryRows, BaseDictionaryRows, PlainBaseDictionaryRows)
        ):
            raise TypeError("original journal family required")
        if version2:
            for record in rows.values():
                if type(record) is not dict or set(record) != {"body", "receipt"}:
                    raise ValueError("original body/receipt coverage differs")
            return CompactCollection(count=len(rows), sha256=digest(rows))
        budget.reserve(len(rows) * 384, rows=len(rows))
        result = []
        for index, key in enumerate(sorted(rows)):
            record = rows[key]
            if set(record) != {"body", "receipt"}:
                raise ValueError("original body/receipt coverage differs")
            result.append(
                {
                    "ordinal": index,
                    "identity_sha256": digest(key),
                    "body_sha256": digest(record["body"]),
                    "receipt_sha256": None
                    if record["receipt"] is None
                    else digest(record["receipt"]),
                }
            )
        return JournalCollection(count=len(rows), sha256=digest(rows), records=result)

    tables = final["settlement"]["rows"]
    history = final["retained_history"]["records"]
    predicates = final["predicate_journals"]
    if (
        set(tables) != {*SETTLEMENT_TABLES, "execution_outbox"}
        or set(history) != set(HISTORY_FAMILIES)
        or set(predicates) != set(PREDICATE_FAMILIES)
    ):
        raise ValueError("complete final family projection required")
    if version2:
        for name in OPERAND_FAMILIES:
            if not view.journal.owns_sequence(roots["operands"][name], "operand:" + name):
                raise ValueError("original operand collection owner differs")
        for name in ("objects", "sql", "transports"):
            if not view.journal.owns_sequence(roots[name], name):
                raise ValueError("original collection owner differs")
        from scripts.execution_capacity.guest_seal_entry import original_artifacts

        raw_files = raw_bytes = 0
        for _, descriptor in original_artifacts(view.manifest):
            raw_files += 1
            raw_bytes += descriptor["size_bytes"]
        closure = {
            "originals": {
                "encoding": 2,
                "raw_files": raw_files,
                "raw_bytes": raw_bytes,
                "records": view.manifest["records"],
                "occurrences": sum(view.manifest["families"].values()),
            }
        }
    else:
        closure = {
            "shards": view.manifest["shards"],
            "original_records": view.manifest["records"],
            "original_nodes": view.manifest["nodes"],
        }
    return (SafeUnitV2 if version2 else SafeUnit)(
        schema_version=2 if version2 else 1,
        kind=kind,
        origin_sha256=origin_sha256,
        base_sha256=base_sha256,
        manifest_sha256=digest(view.manifest),
        cleanup_sha256=digest(cleanup),
        final_sha256=digest(final),
        **closure,
        families={name: collection(roots["operands"][name]) for name in sorted(OPERAND_FAMILIES)},
        source={name: collection(final["source"][name]) for name in SOURCE_FAMILIES},
        tables={name: collection(rows) for name, rows in tables.items()},
        history={name: journal(rows) for name, rows in history.items()},
        predicates={name: journal(rows) for name, rows in predicates.items()},
        **{name: collection(roots[name]) for name in ("objects", "sql", "transports")},
        **{
            name + "_sha256": digest(final[name])
            for name in ("writers", "storage", "broker", "physical")
        },
        physical_observations=collection(final["physical_observations"]),
    )
