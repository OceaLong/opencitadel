"""Completed query receipts, separate from unfinished raw row producers."""

from scripts.execution_capacity.original_collections import (
    BaseCollectionRows,
    CollectionRows,
    PlainBaseCollectionRows,
    PlainCollectionRows,
)
from scripts.execution_capacity.original_journal import OriginalJournal
from scripts.execution_capacity.source_outputs import _Output


def query_output(owner, *, parent=None, slot=None, expected=None):
    if type(owner) is not OriginalJournal or (parent is None) == (expected is None):
        raise ValueError("actual query output owner and producer/replay mode required")
    owner._usable()
    owner.budget.reserve(256, rows=1, largest=256)
    if expected is not None:
        if (
            type(expected)
            not in (
                CollectionRows,
                PlainCollectionRows,
                BaseCollectionRows,
                PlainBaseCollectionRows,
            )
            or expected.owner is not owner
        ):
            raise ValueError("actual original query output required")
        owner._collection_metadata(expected)
    writer = None if expected is not None else owner.begin_collection(parent, slot)
    return _Output(owner, writer, expected)
