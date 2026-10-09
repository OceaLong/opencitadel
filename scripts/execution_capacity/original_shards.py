"""Incremental typed private original graph with exact durable shard coverage.

A view proves retained bytes/structure only. Actual ledger/base owners must bind
it before it can authorize replay or a successful final evidence unit.
"""

import base64
import json
import os
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError
from scripts.execution_capacity.guest_seal import read_private
from scripts.execution_capacity.ownership import _open_private, _private_directory

ROOTS = frozenset({"cleanup", "operands", "objects", "transports", "sql"})
SHARD_BYTES = 1024 * 1024


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_originals(root, roots, binding, *, budget):
    if set(roots) != ROOTS or root.absolute() != root.resolve():
        raise ValueError("exact original root coverage required")
    root.mkdir(mode=0o700)
    seen, active, descriptors = {}, set(), []
    sequence = node_count = 0
    stream = None
    hashed, length, count, first = None, 0, 0, 0

    def close_shard():
        nonlocal stream
        if stream is not None:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            stream = None
            descriptors.append(
                {
                    "ordinal": len(descriptors),
                    "sha256": hashed.hexdigest(),
                    "bytes": length,
                    "count": count,
                    "first": first,
                    "last": sequence,
                }
            )

    def emit(value):
        nonlocal sequence, stream, hashed, length, count, first
        budget.charge(value)
        raw = encode({"sequence": sequence + 1, **value}) + b"\n"
        if len(raw) > SHARD_BYTES:
            raise EvidenceQuotaError("private original frame quota exceeded")
        budget.reserve(len(raw), rows=0)
        if stream is None or length + len(raw) > SHARD_BYTES:
            close_shard()
            stream = os.fdopen(
                _open_private(
                    root / f"{len(descriptors):06}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                ),
                "wb",
            )
            hashed, length, count, first = sha256(), 0, 0, sequence + 1
        stream.write(raw)
        hashed.update(raw)
        length += len(raw)
        count += 1
        sequence += 1

    def visit(value):
        nonlocal node_count
        container = (
            isinstance(value, (dict, list, tuple))
            or is_dataclass(value)
            or hasattr(type(value), "model_fields")
        )
        identity = id(value)
        if container and identity in active:
            raise ValueError("cyclic private original")
        if container and identity in seen:
            return seen[identity]
        budget.reserve(128, rows=1)
        node = node_count
        node_count += 1
        if container:
            seen[identity] = node
            active.add(identity)
            if is_dataclass(value):
                members = ((f.name, getattr(value, f.name)) for f in fields(value))
                size, kind = len(fields(value)), "dict"
            elif hasattr(type(value), "model_fields"):
                members = ((key, getattr(value, key)) for key in type(value).model_fields)
                size, kind = len(type(value).model_fields), "dict"
            elif isinstance(value, dict):
                members, size, kind = value.items(), len(value), "dict"
            else:
                members, size, kind = enumerate(value), len(value), "list"
            emit({"kind": kind, "node": node, "length": size})
            for key, child in members:
                if kind == "dict" and not isinstance(key, str):
                    raise ValueError("original dictionary keys must be strings")
                child_node = visit(child)
                emit({"kind": "member", "node": node, "key": key, "child": child_node})
            active.remove(identity)
        elif isinstance(value, bytes):
            emit({"kind": "bytes", "node": node, "length": len(value)})
            for offset in range(0, len(value), 64 * 1024):
                budget.reserve(min(64 * 1024, len(value) - offset) * 3, rows=0)
                emit(
                    {
                        "kind": "bytes-chunk",
                        "node": node,
                        "offset": offset,
                        "value": base64.b64encode(value[offset : offset + 64 * 1024]).decode(
                            "ascii"
                        ),
                    }
                )
        else:
            scalar_type = "scalar"
            if isinstance(value, (UUID, Decimal, datetime, date)):
                scalar_type = {
                    UUID: "uuid",
                    Decimal: "decimal",
                    datetime: "datetime",
                    date: "date",
                }[type(value)]
                value = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
            elif value is not None and not isinstance(value, (str, bool, int, float)):
                raise ValueError("unsupported original scalar")
            if isinstance(value, str):
                budget.check(len(value) * 12 + 128, rows=1)
            emit({"kind": "value", "node": node, "type": scalar_type, "value": value})
        return node

    try:
        root_nodes = {family: visit(roots[family]) for family in sorted(ROOTS)}
        close_shard()
        manifest = {
            "schema": 1,
            "state": "complete",
            "binding": binding,
            "roots": root_nodes,
            "records": sequence,
            "nodes": node_count,
            "shards": descriptors,
        }
        budget.charge(manifest)
        raw = encode(manifest)
        if len(raw) > SHARD_BYTES:
            raise EvidenceQuotaError("private original manifest quota exceeded")
        with os.fdopen(
            _open_private(root / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
        ) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(root)
        _sync_directory(root.parent)
        return manifest
    finally:
        if stream is not None:
            stream.close()


class OriginalView:
    @classmethod
    def open(cls, root, *, budget, index_bytes=None, verified_base=None):
        _private_directory(root)
        manifest = read_private(root / "manifest.json", budget=budget, max_bytes=SHARD_BYTES)
        if (
            type(manifest) is dict
            and type(manifest.get("schema")) is int
            and manifest["schema"] == 2
        ):
            from scripts.execution_capacity.original_journal import OriginalJournal

            if index_bytes is None:
                raise ValueError("explicit original index quota required")
            journal = OriginalJournal.open(
                root, budget=budget, index_bytes=index_bytes, verified_base=verified_base
            )
            try:
                if not journal.manifest["original_roots"] or journal.manifest != manifest:
                    raise ValueError("complete original root closure differs")
                result = cls()
                result.root, result.manifest, result.budget = root, manifest, budget
                result.journal = journal
                return result
            except BaseException:
                journal.close()
                raise
        if (
            set(manifest) != {"schema", "state", "binding", "roots", "records", "nodes", "shards"}
            or manifest["schema"] != 1
            or manifest["state"] != "complete"
            or set(manifest["roots"]) != ROOTS
        ):
            raise ValueError("invalid original manifest")
        if (
            any(type(manifest[key]) is not int or manifest[key] < 1 for key in ("nodes", "records"))
            or not isinstance(manifest["shards"], list)
            or not 1 <= len(manifest["shards"]) <= 999999
            or any(
                type(node) is not int or not 0 <= node < manifest["nodes"]
                for node in manifest["roots"].values()
            )
        ):
            raise ValueError("invalid original graph counts")
        budget.reserve(len(manifest["shards"]) * 256, rows=len(manifest["shards"]))
        expected = {"manifest.json"} | {f"{n:06}.jsonl" for n in range(len(manifest["shards"]))}
        actual = set()
        for path in root.iterdir():
            budget.reserve(256, rows=1)
            if path.name not in expected:
                raise ValueError("original shard membership differs")
            actual.add(path.name)
        if actual != expected:
            raise ValueError("original shard membership differs")
        result = cls()
        result.root, result.manifest, result.budget = root, manifest, budget
        for _ in result.records():
            pass
        return result

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()

    def close(self):
        journal = getattr(self, "journal", None)
        if journal is not None:
            journal.close()

    def records(self):
        if hasattr(self, "journal"):
            raise ValueError("versioned originals require ordered root streams")
        sequence = 0
        for ordinal, descriptor in enumerate(self.manifest["shards"]):
            if (
                set(descriptor) != {"ordinal", "sha256", "bytes", "count", "first", "last"}
                or any(
                    type(descriptor[key]) is not int
                    for key in ("ordinal", "bytes", "count", "first", "last")
                )
                or descriptor["count"] < 1
                or descriptor["last"] != descriptor["first"] + descriptor["count"] - 1
                or descriptor["ordinal"] != ordinal
                or descriptor["first"] != sequence + 1
                or not 0 < descriptor["bytes"] <= SHARD_BYTES
            ):
                raise ValueError("invalid original shard descriptor")
            with os.fdopen(
                _open_private(self.root / f"{ordinal:06}.jsonl", os.O_RDONLY), "rb"
            ) as stream:
                before = os.fstat(stream.fileno())
                if before.st_size != descriptor["bytes"]:
                    raise ValueError("original shard size differs")
                self.budget.reserve(before.st_size, rows=0)
                raw = stream.read(SHARD_BYTES + 1)
                if len(raw) != before.st_size or sha256(raw).hexdigest() != descriptor["sha256"]:
                    raise ValueError("original shard hash differs")
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ValueError("original shard changed while reading")
            import io

            count = 0
            for line in io.BytesIO(raw):
                self.budget.charge_bytes(len(line))
                if not line.endswith(b"\n"):
                    raise ValueError("incomplete original frame")
                self.budget.reserve(64 * len(line), rows=0)
                row = json.loads(line)
                sequence += 1
                count += 1
                if (
                    not isinstance(row, dict)
                    or type(row.get("sequence")) is not int
                    or row.pop("sequence") != sequence
                ):
                    raise ValueError("original frame sequence differs")
                yield row
            if count != descriptor["count"] or sequence != descriptor["last"]:
                raise ValueError("original shard count differs")
        if sequence != self.manifest["records"]:
            raise ValueError("original complete coverage differs")

    def materialize(self):
        if hasattr(self, "journal"):
            # Per-occurrence typed values are bounded; collection containers
            # remain cursor-backed. Never rebuild the full acquisition graph.
            return self.journal.roots()
        nodes, lengths, edges, complete = {}, {}, {}, set()
        for record in self.records():
            if (
                not isinstance(record, dict)
                or type(record.get("node")) is not int
                or "kind" not in record
            ):
                raise ValueError("invalid original graph record")
            kind, node = record["kind"], record["node"]
            self.budget.reserve(128, rows=1)
            if kind in ("dict", "list", "bytes", "value"):
                if node != len(nodes) or node >= self.manifest["nodes"]:
                    raise ValueError("duplicate or nonsequential original node")
                edges[node] = []
                if kind == "value":
                    if set(record) != {"kind", "node", "type", "value"}:
                        raise ValueError("invalid original scalar record")
                    converters = {
                        "uuid": UUID,
                        "decimal": Decimal,
                        "datetime": datetime.fromisoformat,
                        "date": date.fromisoformat,
                    }
                    value, typed = record["value"], record["type"]
                    if typed == "scalar":
                        if value is not None and type(value) not in (str, bool, int, float):
                            raise ValueError("invalid original scalar type")
                        nodes[node] = value
                    elif typed in converters and isinstance(value, str):
                        nodes[node] = converters[typed](value)
                    else:
                        raise ValueError("unknown original scalar type")
                    complete.add(node)
                else:
                    if (
                        set(record) != {"kind", "node", "length"}
                        or type(record["length"]) is not int
                        or record["length"] < 0
                    ):
                        raise ValueError("invalid original container record")
                    size = record["length"]
                    self.budget.reserve(size * (2 if kind == "bytes" else 128), rows=0)
                    nodes[node] = {} if kind == "dict" else [] if kind == "list" else bytearray()
                    lengths[node] = size
                    if not size:
                        complete.add(node)
                        if kind == "bytes":
                            nodes[node] = b""
            elif kind == "member":
                if (
                    set(record) != {"kind", "node", "key", "child"}
                    or type(record["child"]) is not int
                    or record["child"] not in complete
                    or node not in lengths
                    or node in complete
                ):
                    raise ValueError("invalid original member reference")
                parent, key = nodes[node], record["key"]
                if len(parent) >= lengths[node]:
                    raise ValueError("original container length exceeded")
                if isinstance(parent, list):
                    if type(key) is not int or key != len(parent):
                        raise ValueError("original list order differs")
                    parent.append(nodes[record["child"]])
                elif isinstance(parent, dict) and isinstance(key, str) and key not in parent:
                    parent[key] = nodes[record["child"]]
                else:
                    raise ValueError("duplicate original member")
                edges[node].append(record["child"])
                if len(parent) == lengths[node]:
                    complete.add(node)
            elif kind == "bytes-chunk":
                if (
                    set(record) != {"kind", "node", "offset", "value"}
                    or node not in lengths
                    or node in complete
                ):
                    raise ValueError("invalid original byte frame")
                parent = nodes[node]
                if (
                    not isinstance(parent, bytearray)
                    or type(record["offset"]) is not int
                    or record["offset"] != len(parent)
                    or not isinstance(record["value"], str)
                    or not 0 < len(record["value"]) <= 87384
                ):
                    raise ValueError("invalid original byte frame")
                self.budget.reserve(len(record["value"]) * 2, rows=0)
                data = base64.b64decode(record["value"], validate=True)
                if not data or len(parent) + len(data) > lengths[node]:
                    raise ValueError("original byte length exceeded")
                parent.extend(data)
                if len(parent) == lengths[node]:
                    nodes[node] = bytes(parent)
                    complete.add(node)
            else:
                raise ValueError("unknown original node kind")
        if len(nodes) != self.manifest["nodes"] or len(complete) != len(nodes):
            raise ValueError("incomplete original graph")
        reached, pending = set(), list(self.manifest["roots"].values())
        while pending:
            node = pending.pop()
            if node in reached:
                continue
            if node not in nodes:
                raise ValueError("unknown original root")
            reached.add(node)
            pending.extend(edges[node])
        if len(reached) != len(nodes):
            raise ValueError("orphan original node")
        return {family: nodes[node] for family, node in self.manifest["roots"].items()}
