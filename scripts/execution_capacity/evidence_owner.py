"""One conservative acquisition/retention/assembly budget per evidence unit."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from scripts.acceptance.capacity_c2c_models import OPERAND_FAMILIES as FAMILIES
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.original_dictionaries import (
    BaseDictionaryRows,
    DictionaryRows,
    PlainBaseDictionaryRows,
    PlainDictionaryRows,
)
from scripts.execution_capacity.original_segments import DEFAULT_CHUNK_BYTES
from sqlalchemy import inspect


class EvidenceOwner:
    @classmethod
    def from_limits(cls, root, limits):
        from scripts.execution_capacity.evidence_bounds import parse_evidence_limits
        from scripts.execution_capacity.ownership import _private_directory

        limits = parse_evidence_limits(limits)
        _private_directory(root)
        budget = EvidenceBudget(
            **{key: limits[key] for key in ("bytes_limit", "rows_limit", "row_limit")}
        )
        result = cls(
            budget=budget, original_root=root / "c2c-originals", index_bytes=limits["index_bytes"]
        )
        result.limits = limits
        return result

    def __enter__(self):
        if self.journal is not None:
            self.journal._usable()
        return self

    def __exit__(self, *_):
        if self.journal is not None:
            self.journal.close()

    def __init__(
        self, *, budget=None, original_root=None, index_bytes=None, chunk_bytes=DEFAULT_CHUNK_BYTES
    ):
        self.budget = (
            budget
            if budget is not None
            else EvidenceBudget(
                bytes_limit=32 * 1024 * 1024, rows_limit=65_536, row_limit=4 * 1024 * 1024
            )
        )
        # This prepaid allowance is part of the same unit total. It can only
        # preserve explicit incomplete failure prefixes, never certify success.
        failure_bytes = max(1, min(2 * 1024 * 1024, self.budget.bytes_limit // 8))
        failure_rows = max(1, min(1024, self.budget.rows_limit // 8))
        self.budget.reserve(failure_bytes, rows=failure_rows)
        self.failure_budget = EvidenceBudget(
            bytes_limit=failure_bytes, rows_limit=failure_rows, row_limit=self.budget.row_limit
        )
        self.originals = {name: [] for name in sorted(FAMILIES)}
        self.sql_reads = []
        self.session_count = 0
        self.journal = None
        self._cleanup_token = None
        if original_root is not None:
            from scripts.execution_capacity.original_journal import OriginalJournal

            if index_bytes is None:
                raise ValueError("explicit acquisition index quota required")
            self.journal = OriginalJournal.create(
                original_root, budget=self.budget, index_bytes=index_bytes, chunk_bytes=chunk_bytes
            )
            self.originals = {
                name: self.journal.sequence("operand:" + name) for name in sorted(FAMILIES)
            }
            self.sql_reads = self.journal.sequence("sql")

    def begin_sql(self, record):
        if self.journal is not None:
            return self.journal.begin("sql", record)
        ordinal = len(self.sql_reads)
        self.sql_reads.append(record)
        return ordinal

    def finish_originals(self, roots, binding):
        if (
            self.journal is None
            or set(roots) != {"cleanup", "operands", "objects", "sql", "transports"}
            or roots["operands"] is not self.originals
            or roots["sql"] is not self.sql_reads
            or not all(
                self.journal.owns_sequence(roots[name], name) for name in ("objects", "transports")
            )
        ):
            raise ValueError("actual acquisition owner roots required")
        if self._cleanup_token is None:
            self.journal.append("cleanup", roots["cleanup"])
        else:
            self.journal.complete(self._cleanup_token, roots["cleanup"])
        return self.journal.seal(binding, original_roots=True)

    def begin_cleanup(self):
        if self.journal is not None:
            if self._cleanup_token is not None:
                raise ValueError("actual cleanup acquisition already begun")
            self._cleanup_token = self.journal.begin("cleanup", {"phase": "quiescence"})

    def retain_cleanup_prefix(self, root, value):
        from scripts.execution_capacity.guest_seal import write_private

        if self.journal is None:
            return write_private(root / "quiescence-private.json", value, budget=self.budget)
        if self.journal.root != root / "c2c-originals" or self._cleanup_token is None:
            raise ValueError("actual cleanup prefix owner required")
        self.journal.note(self._cleanup_token, value)
        wire = {
            "schema": "opencitadel.acquisition-prefix.v2",
            "complete": False,
            "original_prefix": {
                "namespace": "local-acquisition-v2",
                "directory": "c2c-originals",
                "family": "cleanup",
                "ordinal": self._cleanup_token.ordinal,
                "records": self.journal.records,
            },
        }
        return write_private(root / "quiescence-private.json", wire, budget=self.failure_budget)

    def complete_sql(self, token, record):
        if self.journal is not None:
            self.journal.complete(token, record)
        elif self.sql_reads[token] is not record:
            raise ValueError("original SQL owner differs")

    def sql_ordinal(self, token, record):
        if self.journal is None:
            if (
                type(token) is not int
                or not 0 <= token < len(self.sql_reads)
                or self.sql_reads[token] is not record
            ):
                raise ValueError("original SQL owner differs")
            return token
        ordinal = self.journal.ordinal(token, family="sql")
        # Exact typed equality; Python equality alone conflates True and 1.
        from itertools import zip_longest

        from scripts.execution_capacity.original_journal import _typed_parts

        saved = self.sql_reads[ordinal]
        if any(
            a != b
            for a, b in zip_longest(
                _typed_parts(saved, self.budget), _typed_parts(record, self.budget)
            )
        ):
            raise ValueError("original SQL observation differs")
        return ordinal

    @contextmanager
    def _bundle(self, family, identity, objects):
        start = {name: len(rows) for name, rows in self.originals.items() if name != family}
        sql_start, object_start = len(self.sql_reads), len(objects)
        self.reserve_state(len(start), bytes_per_item=256)
        error = None
        try:
            yield
        except BaseException as caught:
            error = type(caught).__name__
            raise
        finally:
            self.retain(
                family,
                {
                    "identity": identity,
                    "ranges": {
                        name: [value, len(self.originals[name])] for name, value in start.items()
                    },
                    "sql": [sql_start, len(self.sql_reads)],
                    "objects": [object_start, len(objects)],
                    "error": error,
                },
            )

    def run_inputs(self, *, run_id, scope, kind, objects):
        return self._bundle("run-input", {"run_id": run_id, "scope": scope, "kind": kind}, objects)

    def version_inputs(self, *, batch, parent, scope, principal, objects):
        return self._bundle(
            "version-input",
            {"batch": batch, "parent": parent, "scope": scope, "principal": principal},
            objects,
        )

    def source_inputs(self, *, binding, seed, origin, build_groups, objects):
        return self._bundle(
            "source-input",
            {"binding": binding, "seed": seed, "origin": origin, "build_groups": build_groups},
            objects,
        )

    def pinned_inputs(self, *, scope, principal, identity, objects):
        return self._bundle(
            "pinned-input", {"scope": scope, "principal": principal, "id": identity}, objects
        )

    def final_inputs(self, *, binding, bucket, origin, base, objects):
        return self._bundle(
            "final-input",
            {"binding": binding, "bucket": bucket, "origin": origin, "base": base},
            objects,
        )

    def reserve_state(self, items, *, bytes_per_item=256):
        if (
            type(items) is not int
            or items < 0
            or type(bytes_per_item) is not int
            or bytes_per_item < 1
        ):
            raise ValueError("invalid private state allocation")
        self.budget.reserve(items * bytes_per_item, rows=items)

    def retain(self, family, value):
        if family not in FAMILIES:
            raise ValueError("unknown private original family")
        copied = self._copy(value)
        if self.journal is None:
            self.originals[family].append(copied)
        else:
            self.journal.append("operand:" + family, copied)

    def _copy(self, value):
        return copy_original(value, budget=self.budget, owner=self.journal)

    def retain_failure(self, root, error, *, resources=None):
        """Prepaid, explicitly incomplete private prefix after acquisition fails."""
        import base64

        from scripts.execution_capacity.guest_seal import write_private

        if self.journal is not None:
            if self.journal.root != root / "c2c-originals":
                raise ValueError("failed original prefix must remain in its actual owner root")
            document = {
                "schema": "opencitadel.acquisition-failure.v2",
                "state": "failed",
                "complete": False,
                "error": type(error).__name__,
                "original_prefix": {
                    "namespace": "local-acquisition-v2",
                    "directory": "c2c-originals",
                    "records": self.journal.records,
                    "body_bytes": self.journal.body_bytes,
                    "log_bytes": self.journal.log_bytes,
                    "sealed": self.journal.sealed,
                },
                "original_coverage_incomplete": True,
            }
            return write_private(
                root / "c2c-failed-originals.json", document, budget=self.failure_budget
            )
        frames = () if resources is None else resources.evidence_transport.originals
        count = min(len(frames), self.failure_budget.bytes_limit // 262144)
        prefixes = []
        for frame in frames[:count]:
            self.failure_budget.reserve(32768, rows=1)
            prefixes.append(
                {
                    "start_ns": frame.get("start_ns"),
                    "end_ns": frame.get("end_ns"),
                    "returncode": frame.get("returncode"),
                    "error": frame.get("error"),
                    **{
                        name: {
                            "observed_bytes": len(frame[name]),
                            "prefix_base64": base64.b64encode(frame[name][:4096]).decode("ascii"),
                            "truncated": len(frame[name]) > 4096,
                        }
                        for name in ("stdout", "stderr")
                    },
                }
            )
        document = {
            "schema": 1,
            "state": "failed",
            "complete": False,
            "error": type(error).__name__,
            "transport_count": len(frames),
            "omitted_transports": len(frames) - count,
            "transport_prefixes": prefixes,
            "operand_counts": {key: len(value) for key, value in self.originals.items()},
            "original_coverage_incomplete": True,
        }
        return write_private(
            root / "c2c-failed-originals.json", document, budget=self.failure_budget
        )


def copy_original(value, *, budget, owner=None):
    def copy_value(item):
        return copy_original(item, budget=budget, owner=owner)

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
        if owner is None:
            raise ValueError("explicit original collection owner required")
        owner._collection_metadata(value)
        return value
    # Charge nodes/copies before extending any collection. ORM row values
    # already crossed the checked input boundary; no arbitrary driver read.
    budget.reserve(64, rows=1)
    if value is None or isinstance(value, (bool, int, float, UUID, Decimal, date, datetime)):
        return value
    if isinstance(value, (str, bytes)):
        budget.reserve(len(value) * (4 if isinstance(value, str) else 1), rows=0)
        return value
    if isinstance(value, Mapping):
        return {copy_value(k): copy_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [copy_value(v) for v in value]
    if hasattr(value, "_mapping"):
        return copy_value(dict(value._mapping))
    if is_dataclass(value):
        return {f.name: copy_value(getattr(value, f.name)) for f in fields(value)}
    if hasattr(type(value), "model_fields"):
        return {name: copy_value(getattr(value, name)) for name in type(value).model_fields}
    mapped = inspect(value, raiseerr=False)
    if mapped is not None and hasattr(mapped, "mapper"):
        return {
            column.key: copy_value(getattr(value, column.key))
            for column in mapped.mapper.column_attrs
        }
    raise ValueError("unsupported private original type")
