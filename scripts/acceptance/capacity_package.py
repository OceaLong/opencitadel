"""One private indexed session for complete, independently validated public shards."""

import hashlib
import json
import operator
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import zip_longest
from typing import ClassVar, get_args, get_origin

from pydantic import TypeAdapter
from scripts.acceptance.capacity_index import CapacityIndex
from scripts.acceptance.capacity_io import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    MAX_PACKAGE_BYTES,
    SHARDED,
    read_relative,
    safe_path,
    strict_json,
)
from scripts.acceptance.capacity_models import ROLES, Artifact, Fixture
from scripts.acceptance.capacity_role_rows import ROLE_ROWS
from scripts.execution_capacity.evidence_bounds import EvidenceBudget


@dataclass(frozen=True)
class PublicConsumerResources:
    budget: EvidenceBudget
    index_bytes: int

    def __post_init__(self):
        if (
            type(self.budget) is not EvidenceBudget
            or type(self.index_bytes) is not int
            or self.index_bytes < 32768
        ):
            raise ValueError("explicit trusted public consumer resources required")
        if self.index_bytes > self.budget.bytes_limit or self.index_bytes % 4096:
            raise ValueError("public index exceeds trusted resources")


class PublicRows(Sequence):
    __slots__ = ("_field", "_owner", "_range", "_role")

    def __init__(self, owner, role, field, positions):
        self._owner, self._role, self._field, self._range = owner, role, field, positions

    def __len__(self):
        self._owner._usable()
        return len(self._range)

    def __getitem__(self, key):
        self._owner._usable()
        if isinstance(key, slice):
            return PublicRows(self._owner, self._role, self._field, self._range[key])
        ordinal = self._range[operator.index(key)]
        return self._owner._row(self._role, self._field, ordinal)

    def __eq__(self, other):
        self._owner._usable()
        if type(other) not in (list, tuple, PublicRows):
            return False
        missing, equal = object(), True
        for left, right in zip_longest(self, other, fillvalue=missing):
            if left is missing or right is missing or left != right:
                equal = False
        return equal


class PackageSession:
    models: ClassVar = {**ROLES, "fixture": Fixture}

    def __init__(self, artifacts, root, *, resources):
        if type(resources) is not PublicConsumerResources:
            raise TypeError("trusted public consumer resources required")
        if not len(ROLES) + 1 <= len(artifacts) <= MAX_ARTIFACTS:
            raise ValueError("bounded artifact count requires every safe role")
        if any(type(item) is not Artifact for item in artifacts):
            raise TypeError("strict actual artifact descriptors required")
        self.resources, self.root = resources, root
        self.budget = resources.budget
        self._closed, self._sealed = False, False
        self._roles, self._adapters, self._seen_roles = {}, {}, set()
        self._unique = {}
        self._relation_serial = 0
        # The descriptor ledger is explicitly bounded by MAX_ARTIFACTS.
        self.budget.reserve(len(artifacts) * 2048 + resources.index_bytes, rows=len(artifacts))
        self.artifacts = tuple(artifacts)
        self.index = CapacityIndex(quota_bytes=resources.index_bytes, row_bytes=MAX_ARTIFACT_BYTES)
        try:
            paths, shards, total = set(), {}, 0
            for item in artifacts:
                if item.path in paths:
                    raise ValueError("duplicate artifact path")
                paths.add(item.path)
                if item.schema_version != (1 if item.role == "fixture" else 3):
                    raise ValueError("unsupported artifact role version")
                if item.size_bytes > MAX_ARTIFACT_BYTES:
                    raise ValueError("artifact exceeds size limit")
                total += item.size_bytes
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("package exceeds size limit")
                if item.role not in SHARDED and (item.shard_count != 1 or item.shard_index != 0):
                    raise ValueError("role is not shardable")
                count, seen = shards.setdefault(item.role, (item.shard_count, set()))
                if (
                    count != item.shard_count
                    or item.shard_index in seen
                    or item.shard_index >= count
                ):
                    raise ValueError("duplicate/inconsistent artifact shard")
                seen.add(item.shard_index)
                self._ingest(item)
            if self._seen_roles != set(self.models) or any(
                len(seen) != count for count, seen in shards.values()
            ):
                raise ValueError("missing capacity artifact role/shard")
            self._sealed = True
            for role, model in self.models.items():
                values = {}
                for name, field in model.model_fields.items():
                    if get_origin(field.annotation) is list:
                        values[name] = PublicRows(
                            self, role, name, range(self.index.count(self._stream(role, name)))
                        )
                    else:
                        values[name] = self._row(role, name, 0)
                self._roles[role] = ROLE_ROWS[role](self, role, **values)
        except BaseException:
            self.close()
            raise

    def _usable(self):
        if self._closed or not self._sealed:
            raise ValueError("public package session is closed or incomplete")

    def _stream(self, role, field):
        if role not in self.models or field not in self.models[role].model_fields:
            raise ValueError("fixed public role field required")
        return "public:" + role + ":" + field

    def _adapter(self, role, name):
        key = role, name
        if key not in self._adapters:
            field = self.models[role].model_fields[name]
            annotation = (
                get_args(field.annotation)[0]
                if get_origin(field.annotation) is list
                else field.rebuild_annotation()
            )
            self._adapters[key] = TypeAdapter(annotation)
        return self._adapters[key]

    def _row(self, role, name, ordinal):
        self._usable()
        raw = self.index.find(self._stream(role, name), "ordinal", str(ordinal))
        if raw is None:
            raise IndexError(ordinal)
        self.budget.reserve(len(raw) * 64, rows=1)
        return self._adapter(role, name).validate_python(strict_json(raw))

    def _ingest(self, item):
        # One complete capped shard is validated before any indexed role can be
        # published. Nested arrays remain inside this prepaid parse lifetime.
        self.budget.reserve(item.size_bytes * 64, rows=1)
        safe_path(self.root, item.path)
        data = read_relative(self.root, item.path, item.size_bytes)
        if len(data) != item.size_bytes or hashlib.sha256(data).hexdigest() != item.sha256:
            raise ValueError("capacity artifact digest mismatch")
        document = strict_json(data)
        if (
            type(document) is not dict
            or "schema_version" not in document
            or (item.role != "fixture" and document.get("role") != item.role)
        ):
            raise ValueError("artifact content role/schema missing or mismatched")
        model = self.models[item.role]
        parsed = model.model_validate(document)
        prior = item.role in self._seen_roles
        if prior and item.role not in SHARDED:
            raise ValueError("duplicate artifact role")
        for name, field in model.model_fields.items():
            value = getattr(parsed, name)
            is_list = get_origin(field.annotation) is list
            entries = value if is_list else (value,)
            stream = self._stream(item.role, name)
            repeated = prior and name not in SHARDED[item.role]
            if repeated and self.index.count(stream) != len(entries):
                raise ValueError("shard role identity mismatch")
            for position, entry in enumerate(entries):
                adapter = self._adapter(item.role, name)
                raw = json.dumps(
                    adapter.dump_python(entry, mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
                if repeated:
                    saved = self.index.find(stream, "ordinal", str(position))
                    if saved != raw:
                        raise ValueError("shard role identity mismatch")
                else:
                    self.budget.reserve(len(raw) * 2 + 256, rows=1)
                    ordinal = self.index.count(stream)
                    self.index.append(stream, raw, keys={"ordinal": str(ordinal)})
        self._seen_roles.add(item.role)
        # No data/document/parsed escapes this function or survives next shard.

    @property
    def roles(self):
        self._usable()
        return dict(self._roles)

    def owns_role(self, value, name):
        self._usable()
        return self._roles.get(name) is value and type(value) is ROLE_ROWS[name]

    def start_private_comparison(self):
        from scripts.execution_capacity.public_replay_comparator import PublicReplayComparator

        self._usable()
        self.budget.reserve(2048, rows=1)
        self._private_comparison = PublicReplayComparator(self)
        return self._private_comparison

    def read_source(self, item):
        self._usable()
        if type(item) is not Artifact or not any(item is saved for saved in self.artifacts):
            raise ValueError("actual package source descriptor required")
        self.budget.reserve(item.size_bytes, rows=1)
        data = read_relative(self.root, item.path, item.size_bytes)
        if len(data) != item.size_bytes or hashlib.sha256(data).hexdigest() != item.sha256:
            raise ValueError("capacity artifact changed after ingestion")
        return data

    def _bounded_role_dump(self, role):
        self._usable()
        if role != "environment":
            raise ValueError("fixed bounded public control required")
        result = {}
        for name, field in self.models[role].model_fields.items():
            value = getattr(self._roles[role], name)
            result[name] = (
                [self._adapter(role, name).dump_python(row, mode="json") for row in value]
                if get_origin(field.annotation) is list
                else self._adapter(role, name).dump_python(value, mode="json")
            )
        return result

    def close(self):
        if not self._closed:
            self._closed = True
            self.index.close()

    def __enter__(self):
        self._usable()
        return self

    def __exit__(self, *_):
        self.close()


class PublicUnique:
    """Recomputable same-session lookup; original rows remain the authority."""

    __slots__ = ("_key", "_rows", "_stream")

    def __init__(self, rows, key, stream):
        self._rows, self._key, self._stream = rows, key, stream

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        for row in self._rows:
            yield getattr(row, self._key)

    def __getitem__(self, key):
        owner = self._rows._owner
        owner._usable()
        if type(key) is not str:
            raise KeyError(key)
        owner.budget.reserve(len(key) * 12 + 128, rows=0)
        encoded = json.dumps(key, ensure_ascii=True)
        raw = owner.index.find(self._stream, "key", encoded)
        if raw is None:
            raise KeyError(key)
        row = self._rows[int(raw)]
        if getattr(row, self._key) != key:
            raise ValueError("public original identity changed")
        return row

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def values(self):
        return iter(self._rows)

    def items(self):
        for row in self._rows:
            yield getattr(row, self._key), row

    def __eq__(self, other):
        if type(other) not in (dict, PublicUnique):
            return False
        equal = len(self) == len(other)
        for key, row in self.items():
            if key not in other or row != other[key]:
                equal = False
        return equal


def public_unique(rows, key):
    if type(rows) is not PublicRows:
        raise TypeError("actual finite public rows required")
    owner = rows._owner
    owner._usable()
    # Fixed callsite inventory from the shared derivation/physical predicates.
    identities = {
        ("protocol", "samples"): ("sample_id", "action_id"),
        ("protocol", "windows"): ("window_id",),
        ("measurements", "samples"): ("sample_id",),
        ("measurements", "sources"): ("source_id",),
        ("measurements", "browsers"): ("browser_id",),
        ("measurements", "resources"): ("sample_id",),
        ("measurements", "markers"): ("marker_id",),
        ("measurements", "live_paints"): ("progress_id",),
        ("workload", "windows"): ("window_id",),
        ("workload", "progress"): ("progress_id", "event_id"),
        ("workload", "source_acks"): ("progress_id",),
        ("seal", "images"): ("image_id",),
        ("seal", "cohorts"): ("cohort_id",),
        ("cleanup", "cohorts"): ("cohort_id",),
        ("cleanup", "dispositions"): ("resource_id",),
        ("resets", "resets"): ("reset_id",),
        ("diagnostics", "queries"): ("sample_id",),
    }
    if type(key) is not str or key not in identities.get((rows._role, rows._field), ()):
        raise ValueError("fixed original identity field required")
    signature = rows._role, rows._field, rows._range, key
    if signature in owner._unique:
        return owner._unique[signature]
    owner.budget.reserve(1024, rows=1)
    stream = "public-unique:" + str(owner._relation_serial)
    owner._relation_serial += 1
    duplicate = False
    for position, row in enumerate(rows):
        identity = getattr(row, key)
        if type(identity) is not str:
            raise TypeError("public original ID field required")
        owner.budget.reserve(len(identity) * 12 + 256, rows=1)
        encoded = json.dumps(identity, ensure_ascii=True)
        if owner.index.find(stream, "key", encoded) is not None:
            duplicate = True
            continue
        owner.index.append(stream, str(position).encode(), keys={"key": encoded})
    if duplicate:
        raise ValueError(f"duplicate {key}")
    result = PublicUnique(rows, key, stream)
    owner._unique[signature] = result
    return result
