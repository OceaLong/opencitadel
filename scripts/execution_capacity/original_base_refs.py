"""A journal owns one independently verified immutable base namespace."""

from scripts.acceptance.capacity_io import strict_json
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

_TO_BASE = {
    CollectionRows: BaseCollectionRows,
    PlainCollectionRows: PlainBaseCollectionRows,
    DictionaryRows: BaseDictionaryRows,
    PlainDictionaryRows: PlainBaseDictionaryRows,
}
_TO_SOURCE = {value: key for key, value in _TO_BASE.items()}


def binding(base):
    return {
        "descriptor": base.descriptor(),
        "final": base.base.private["c2c_export"]["final"],
        "image": base.base.private["raw_identity"],
    }


def bind(owner, base):
    from scripts.execution_capacity.original_journal import OriginalJournal
    from scripts.execution_capacity.seal_finalizer import BaseOriginalEvidence, VerifiedBase

    owner._usable()
    if owner._base_evidence is not None:
        raise ValueError("verified base namespace already bound")
    if (
        type(base) is not BaseOriginalEvidence
        or type(base.base) is not VerifiedBase
        or type(getattr(base.view, "journal", None)) is not OriginalJournal
    ):
        raise ValueError("actual verified base evidence required")
    base.view.journal._usable()
    if base.view.journal.verified_base is not None:
        raise ValueError("recursive verified base namespace refused")
    child = base.base.open_evidence(budget=owner.budget)
    try:
        expected = binding(base)
        if encode(binding(child)) != encode(expected) or child.view.manifest != base.view.manifest:
            raise ValueError("independently reopened base binding differs")
        owner.budget.charge(expected)
        owner.verified_base = strict_json(encode(expected))
        owner._base_source = base.view.journal
        owner._base_evidence = child
    except BaseException:
        child.close()
        raise


def checked_base(owner):
    owner._usable()
    child = owner._base_evidence
    if child is None or owner.verified_base is None:
        raise ValueError("explicit verified base namespace required")
    child.view.journal._usable()
    if encode(binding(child)) != encode(owner.verified_base):
        raise ValueError("verified base binding changed")
    return child.view.journal


def namespace(owner):
    return {
        "sealed_artifact": owner.verified_base["descriptor"]["sealed_artifact"],
        "manifest": owner.verified_base["final"]["manifest"],
    }


def _reference_cursor(owner, rows, *, source, scope=None):
    if type(rows) not in _TO_BASE or rows.owner is not source:
        raise ValueError("actual bound base collection required")
    source._collection_metadata(rows)
    descriptor = {
        "namespace": "verified-base",
        "producer": rows.producer_ordinal,
        "count": rows.length,
        "body": rows.body_descriptor,
        "base": namespace(owner),
    }
    owner.budget.charge(descriptor)
    kind = _TO_BASE[type(rows)]
    if scope is not None:
        logical = (
            rows.logical_node if type(rows) in (PlainCollectionRows, PlainDictionaryRows) else None
        )
        if logical is not None and logical[0] == "conversion":
            # Conversion nodes belong to the source owner, not its base consumer.
            # A fresh target conversion gets its own target-issued ordinal.
            return kind(
                owner, rows.producer_ordinal, rows.length, strict_json(encode(descriptor)), scope
            )
        return owner._finite_cursor(
            kind,
            rows.producer_ordinal,
            rows.length,
            strict_json(encode(descriptor)),
            scope,
            logical,
        )
    return kind(owner, rows.producer_ordinal, rows.length, strict_json(encode(descriptor)))


def reference(owner, rows):
    source = checked_base(owner)
    if type(rows) not in _TO_BASE or rows.owner is not owner._base_source:
        raise ValueError("actual bound base collection required")
    rows.owner._collection_metadata(rows)
    equivalent = type(rows)(source, rows.producer_ordinal, rows.length, rows.body_descriptor)
    return _reference_cursor(owner, equivalent, source=source)


def source_rows(owner, rows):
    source = checked_base(owner)
    descriptor = rows.body_descriptor
    if (
        type(rows) not in _TO_SOURCE
        or rows.owner is not owner
        or type(descriptor) is not dict
        or set(descriptor) != {"namespace", "producer", "count", "body", "base"}
        or descriptor["namespace"] != "verified-base"
        or type(descriptor["producer"]) is not int
        or descriptor["producer"] != rows.producer_ordinal
        or type(descriptor["count"]) is not int
        or descriptor["count"] != rows.length
        or encode(descriptor["base"]) != encode(namespace(owner))
    ):
        raise ValueError("exact verified base collection reference required")
    kind = _TO_SOURCE[type(rows)]
    result = kind(source, rows.producer_ordinal, rows.length, descriptor["body"])
    source._collection_metadata(result)
    return result


def metadata(owner, rows):
    result = source_rows(owner, rows)
    return result.owner._collection_metadata(result)


def resolve(owner, descriptor, ordinal, mode, kind, scope, logical_node):
    if type(descriptor) is not dict or type(descriptor.get("count")) is not int:
        raise ValueError("closed verified base collection required")
    cursor = (
        (BaseDictionaryRows if mode is None else PlainBaseDictionaryRows)
        if kind == "dict"
        else (BaseCollectionRows if mode is None else PlainBaseCollectionRows)
    )
    rows = owner._finite_cursor(
        cursor, ordinal, descriptor["count"], descriptor, scope, logical_node
    )
    return rows, metadata(owner, rows)["height"]


def rehome_graph(owner, value, *, scope):
    """A base child value never exposes a cursor owned by the child resource."""
    source = checked_base(owner)
    held, active = {}, set()

    def visit(item, depth=0):
        if depth > 64 or len(held) * 256 > owner.budget.row_limit:
            raise ValueError("base entry root requires finite producers")
        owner.budget.reserve(256, rows=1)
        if type(item) in _TO_BASE:
            return _reference_cursor(owner, item, source=source, scope=scope)
        if type(item) in (dict, list, tuple):
            if id(item) in active:
                raise ValueError("cyclic verified base entry")
            if id(item) in held:
                return held[id(item)][1]
            active.add(id(item))
            result = (
                {key: visit(child, depth + 1) for key, child in item.items()}
                if type(item) is dict
                else [visit(child, depth + 1) for child in item]
            )
            active.remove(id(item))
            held[id(item)] = item, result
            return result
        from scripts.execution_capacity.evidence_owner import copy_original

        return copy_original(item, budget=owner.budget, owner=owner)

    return visit(value)


def reference_graph(owner, value):
    """Bounded held root copy; complete collections become fixed base refs."""
    from scripts.execution_capacity.evidence_owner import copy_original

    held, active = {}, set()

    def visit(item, depth=0):
        if depth > 64 or len(held) * 256 > owner.budget.row_limit:
            raise ValueError("base held root requires finite producers")
        owner.budget.reserve(256, rows=1)
        finite = type(item) in _TO_BASE
        if finite or type(item) in (dict, list, tuple):
            if id(item) in active:
                raise ValueError("cyclic verified base graph")
            if id(item) in held:
                return held[id(item)][1]
            active.add(id(item))
            if finite:
                result = reference(owner, item)
            elif type(item) is dict:
                result = {key: visit(child, depth + 1) for key, child in item.items()}
            else:
                result = [visit(child, depth + 1) for child in item]
            active.remove(id(item))
            held[id(item)] = item, result
            return result
        return copy_original(item, budget=owner.budget, owner=owner)

    checked_base(owner)
    return visit(value)
