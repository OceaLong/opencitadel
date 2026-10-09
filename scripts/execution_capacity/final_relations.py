"""Fixed final-history joins pointing back to complete original occurrences.

Keys contain full typed values. Groups retain every occurrence; selecting the
last value preserves old dict-comprehension semantics without discarding input.
"""

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
from scripts.execution_capacity.original_journal import OriginalJournal
from scripts.execution_capacity.replay_relations import typed_relation_key

_LISTS = (CollectionRows, PlainCollectionRows, BaseCollectionRows, PlainBaseCollectionRows)
_MAPS = (DictionaryRows, PlainDictionaryRows, BaseDictionaryRows, PlainBaseDictionaryRows)
_FAMILIES = {
    "objects",
    "linked",
    "owners",
    "dispatches",
    "reservations",
    "settlements",
    "outcomes",
    "source_attempts",
    "lease_projection",
    "source_runs",
}


def _key(family, row):
    if family == "source_runs":
        return row["run_id"]
    if family == "source_attempts":
        return (
            str(row["batch_id"]),
            str(row["case_revision_id"]),
            str(row["config_version_id"]),
            row["repetition"],
        )
    if family == "lease_projection":
        return row["id"]
    if family == "objects":
        return row["key"]
    if family == "linked":
        return row["body"].get("port_upload_id")
    if family == "owners":
        return str(row["stream_id"])
    if family == "outcomes":
        return str(row["run_id"])
    return row["scope_key"], str(row["call_identity"])


def _value(family, row):
    if family == "linked":
        return row["body"]
    if family == "owners":
        return row["owner_scope_key"]
    if family == "outcomes":
        return row["status"]
    return row


class FinalRelations:
    def __init__(self, owner, sources):
        if type(owner) is not OriginalJournal or not set(sources) <= _FAMILIES:
            raise ValueError("actual owner and fixed final relations required")
        owner._usable()
        self.owner, self.sources = owner, sources
        owner.budget.reserve(512, rows=1, largest=512)
        session = owner.index.append("final-relation-sessions", b"{}")
        self.prefix = "final-relations:" + str(session) + ":"
        for family, source in sources.items():
            if type(source) in (*_LISTS, *_MAPS):
                if source.owner is not owner:
                    raise ValueError("foreign final relation source")
                owner._collection_metadata(source)
            elif type(source) not in (list, tuple, dict):
                raise ValueError("closed original final relation source required")
            is_map = type(source) in (dict, *_MAPS)
            for locator in source if is_map else range(len(source)):
                row = source[locator]
                key = _key(family, row)
                encoded = self._identity(key)
                locator_bytes = len(locator) * 12 + 128 if type(locator) is str else 128
                owner.budget.reserve(locator_bytes, rows=0, largest=locator_bytes)
                raw = encode(locator)
                owner.budget.reserve(
                    len(raw) + len(encoded) * 2 + 256,
                    rows=1,
                    largest=len(raw) + len(encoded) * 2 + 256,
                )
                stream = self.prefix + family
                owner.index.append(stream + ":all", raw)
                if family == "linked" and key is None:
                    continue
                if owner.index.find(stream, "identity", encoded) is None:
                    owner.index.append(
                        stream, encode([encoded, locator]), keys={"identity": encoded}
                    )
                owner.index.group(stream, encoded, raw)

    def _identity(self, key):
        return typed_relation_key(key, owner=self.owner, budget=self.owner.budget)

    def all(self, family, key):
        self.owner._usable()
        if family not in self.sources:
            raise ValueError("final relation family absent")
        encoded = self._identity(key)
        for raw in self.owner.index.group_rows(self.prefix + family, encoded):
            self.owner.budget.reserve(len(raw) * 8 + 128, rows=1, largest=len(raw) * 8 + 128)
            locator = strict_json(raw)
            row = self.sources[family][locator]
            if self._identity(_key(family, row)) != encoded:
                raise ValueError("original final relation candidate differs")
            yield _value(family, row)
        self.owner._usable()

    def get(self, family, key, default=None):
        selected = default
        for value in self.all(family, key):
            selected = value
        return selected

    def items(self, family):
        self.owner._usable()
        if family not in self.sources:
            raise ValueError("final relation family absent")
        for raw in self.owner.index.rows(self.prefix + family):
            self.owner.budget.reserve(len(raw) * 8 + 128, rows=1, largest=len(raw) * 8 + 128)
            expected, locator = strict_json(raw)
            row = self.sources[family][locator]
            key = _key(family, row)
            if self._identity(key) != expected:
                raise ValueError("original final relation first identity differs")
            yield key, self.get(family, key)
        self.owner._usable()

    def calls(self, runs):
        """Old actual[call_identity] assignment order after run filtering."""
        self.owner._usable()
        self.owner.budget.reserve(256, rows=1, largest=256)
        session = self.owner.index.append("final-disposition-sessions", b"{}")
        stream = self.prefix + "calls:" + str(session)
        for identity, dispatch in self.items("dispatches"):
            if str(dispatch["run_id"]) not in runs:
                continue
            key = self._identity(identity[1])
            self.owner.budget.reserve(len(key) * 3 + 256, rows=1, largest=len(key) * 3 + 256)
            raw = encode(identity)
            if self.owner.index.find(stream, "identity", key) is None:
                self.owner.index.append(stream, raw, keys={"identity": key})
            self.owner.index.group(stream, key, raw)
        for raw in self.owner.index.rows(stream):
            self.owner.budget.reserve(len(raw) * 8 + 128, rows=1, largest=len(raw) * 8 + 128)
            first = strict_json(raw)
            key = self._identity(first[1])
            selected = None
            for candidate in self.owner.index.group_rows(stream, key):
                self.owner.budget.reserve(
                    len(candidate) * 8 + 128, rows=1, largest=len(candidate) * 8 + 128
                )
                identity = tuple(strict_json(candidate))
                dispatch = self.get("dispatches", identity)
                if (
                    dispatch is None
                    or identity[1] != first[1]
                    or str(dispatch["run_id"]) not in runs
                ):
                    raise ValueError("original disposition candidate differs")
                selected = identity, dispatch
            if selected is None:
                raise ValueError("original disposition candidate absent")
            yield first[1], selected
        self.owner._usable()

    def occurrences(self, family):
        self.owner._usable()
        return self.owner.index.count(self.prefix + family + ":all")
