"""Incremental source result outputs; pending writers never provide row reads."""

from scripts.execution_capacity.original_collections import (
    BaseCollectionRows,
    CollectionRows,
    PlainBaseCollectionRows,
    PlainCollectionRows,
)
from scripts.execution_capacity.original_journal import OriginalJournal
from scripts.execution_capacity.replay_relations import same_value

_FAMILIES = ("runs", "objects", "versions", "errors", "cohorts", "safe_reads")


class _Output:
    def __init__(self, owner, writer, expected):
        self.owner, self.writer, self.expected = owner, writer, expected
        self.count, self.completed = 0, None

    def __len__(self):
        self.owner._usable()
        return self.count

    def append(self, value):
        self.owner._usable()
        if self.completed is not None:
            raise ValueError("completed source output is immutable")
        if self.writer is not None:
            self.writer.append(value)
        elif self.count >= len(self.expected) or not same_value(
            value, self.expected[self.count], owner=self.owner, budget=self.owner.budget
        ):
            raise ValueError("original source output row differs")
        self.count += 1

    def extend(self, values):
        for value in values:
            self.append(value)

    def complete(self):
        self.owner._usable()
        if self.completed is not None:
            return self.completed
        if self.writer is not None:
            self.completed = self.writer.complete()
        else:
            if self.count != len(self.expected):
                raise ValueError("original source output count differs")
            self.completed = self.expected
        return self.completed


class SourceOutputs:
    def __init__(self, result, owner, *, parent=None, expected=None):
        if type(owner) is not OriginalJournal or (parent is None) == (expected is None):
            raise ValueError("actual source output owner and producer/replay mode required")
        owner._usable()
        owner.budget.reserve(1536, rows=6, largest=1536)
        self.reads_projected = False
        self.owner, self.parent = owner, parent
        self.result, self.outputs = result, {}
        for family in _FAMILIES:
            value = None if expected is None else expected[family]
            if expected is not None:
                if (
                    type(value)
                    not in (
                        CollectionRows,
                        PlainCollectionRows,
                        BaseCollectionRows,
                        PlainBaseCollectionRows,
                    )
                    or value.owner is not owner
                ):
                    raise ValueError("actual original source output required")
                owner._collection_metadata(value)
            writer = (
                None if expected is not None else owner.begin_collection(parent, "source-" + family)
            )
            output = _Output(owner, writer, value)
            self.outputs[family] = output
            setattr(result, family, output)

    def close_reads(self, reads):
        if (
            type(reads)
            not in (
                CollectionRows,
                PlainCollectionRows,
                BaseCollectionRows,
                PlainBaseCollectionRows,
            )
            or reads.owner is not self.owner
        ):
            raise ValueError("actual completed source query reads required")
        self.owner._collection_metadata(reads)
        if not self.reads_projected:
            from scripts.execution_capacity.inventory import PUBLIC_READ_FIELDS

            for row in reads:
                self.outputs["safe_reads"].append(
                    {key: value for key, value in row.items() if key in PUBLIC_READ_FIELDS}
                )
            self.result.safe_reads = self.outputs["safe_reads"].complete()
            self.reads_projected = True

    def close_data(self):
        for family in ("runs", "objects", "versions"):
            setattr(self.result, family, self.outputs[family].complete())

    def close(self):
        self.close_data()
        self.result.errors = self.outputs["errors"].complete()
        self.result.cohorts = self.outputs["cohorts"].complete()
        self.result.safe_reads = self.outputs["safe_reads"].complete()
