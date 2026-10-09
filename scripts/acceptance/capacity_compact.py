"""Exact compact report statistics; full observations remain in artifacts."""

import hashlib
import json
import math

from scripts.acceptance.capacity_package import PackageSession


class Numbers:
    def __init__(self, owner):
        if type(owner) is not PackageSession:
            raise TypeError("actual bounded package statistics required")
        owner._usable()
        self.owner = owner
        owner.budget.reserve(1024, rows=1)
        self.stream = "public-number:" + str(owner._relation_serial)
        owner._relation_serial += 1
        self.count = 0
        self.maximum = None
        self.over200 = 0
        self.digest = hashlib.sha256(b"[")

    def add(self, value):
        self.owner._usable()
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            raise ValueError("finite original statistic required")
        self.owner.budget.reserve(
            (value.bit_length() if type(value) is int else 64) * 2 + 256, rows=1
        )
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if self.count:
            self.digest.update(b",")
        self.digest.update(raw)
        self.owner.index.number(self.stream, value)
        self.count += 1
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.over200 += value > 200

    def result(self, *, long_tasks=False):
        self.owner._usable()
        digest = self.digest.copy()
        digest.update(b"]")
        result = {"count": self.count, "max_ms": self.maximum, "ordered_sha256": digest.hexdigest()}
        if long_tasks:
            result["over_200ms_count"] = self.over200
        else:
            for name, fraction in [("p50_ms", 0.5), ("p95_ms", 0.95)]:
                result[name] = (
                    None
                    if not self.count
                    else self.owner.index.rank(self.stream, math.ceil(self.count * fraction))
                )
        return result


class CompactResources:
    def __init__(self, owner):
        self.frames, self.long_tasks = Numbers(owner), Numbers(owner)
        self.peak_heap, self.peak_dom = 0, 0

    def add(self, selected):
        self.peak_heap = max(self.peak_heap, max(selected.heap_bytes))
        self.peak_dom = max(self.peak_dom, max(selected.mounted_rows))
        for frame in selected.frames:
            self.frames.add(frame.interval_ms)
        for task in selected.long_tasks_ms:
            self.long_tasks.add(task)

    def result(self):
        return {**self.frames.result(), "long_tasks": self.long_tasks.result(long_tasks=True)}


def ordered_commitment(values, *, owner):
    owner._usable()
    digest = hashlib.sha256(b"[")
    count = 0
    for value in values:
        # Caller transfers only one precharged original leaf/projection at a time.
        owner.budget.charge(value)
        if count:
            digest.update(b",")
        for part in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            owner.budget.reserve(len(part) * 4, rows=0)
            digest.update(part.encode())
        count += 1
    digest.update(b"]")
    return {"count": count, "ordered_sha256": digest.hexdigest()}
