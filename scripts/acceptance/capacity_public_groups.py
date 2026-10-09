"""Fixed public progress/marker joins on the package's existing scratch index."""

import json
import operator
from collections.abc import Sequence

from scripts.acceptance.capacity_package import PublicRows, PublicUnique


class GroupRows(Sequence):
    def __init__(self, group, identity, positions):
        self.group, self.identity, self.positions = group, identity, positions

    def __len__(self):
        self.group.owner._usable()
        return len(self.positions)

    def __getitem__(self, key):
        self.group.owner._usable()
        if isinstance(key, slice):
            return GroupRows(self.group, self.identity, self.positions[key])
        ordinal = self.positions[operator.index(key)]
        raw = self.group.owner.index.ordered_group_at(self.group.stream, self.identity, ordinal)
        return self.group.rows[int(raw)]

    def __iter__(self):
        self.group.owner._usable()
        if self.positions == range(
            self.group.owner.index.ordered_group_count(self.group.stream, self.identity)
        ):
            for raw in self.group.owner.index.ordered_group_rows(self.group.stream, self.identity):
                yield self.group.rows[int(raw)]
        else:
            for position in range(len(self)):
                yield self[position]


class PublicGroups:
    def __init__(self, rows, mode):
        if type(rows) is PublicUnique:
            rows = rows._rows
        if type(rows) is not PublicRows:
            raise TypeError("actual public finite rows required")
        self.owner, self.rows, self.mode = rows._owner, rows, mode
        self.owner._usable()
        required = {
            "progress-window": ("workload", "progress"),
            "progress-run": ("workload", "progress"),
            "markers-window": ("measurements", "markers"),
            "calibrations": ("network", "calibrations"),
        }
        if required.get(mode) != (rows._role, rows._field):
            raise ValueError("fixed public grouping required")
        self.owner.budget.reserve(1024, rows=1)
        self.stream = "public-group:" + str(self.owner._relation_serial)
        self.owner._relation_serial += 1
        for position, row in enumerate(rows):
            key = (
                (row.window_id, row.run_id)
                if mode == "progress-run"
                else (row.window_id, row.phase)
                if mode == "calibrations"
                else (row.window_id,)
            )
            identity = self._identity(key)
            if self.owner.index.find(self.stream + ":keys", "identity", identity) is None:
                self.owner.budget.reserve(len(identity) * 2 + 256, rows=1)
                self.owner.index.append(
                    self.stream + ":keys", identity.encode(), keys={"identity": identity}
                )
            value = (
                row.after_ns
                if mode == "progress-run"
                else row.sequence
                if mode == "markers-window"
                else row.ordinal
                if mode == "calibrations"
                else position
            )
            self.owner.budget.reserve(256, rows=1)
            self.owner.index.ordered_group(self.stream, identity, value, str(position).encode())

    def _identity(self, key):
        self.owner._usable()
        if (
            type(key) is not tuple
            or len(key) != (2 if self.mode in {"progress-run", "calibrations"} else 1)
            or any(type(part) is not str for part in key)
        ):
            raise ValueError("fixed public group key required")
        self.owner.budget.reserve(sum(len(part) * 12 for part in key) + 128, rows=0)
        return json.dumps(key, ensure_ascii=True)

    def __getitem__(self, key):
        identity = self._identity(key)
        return GroupRows(
            self, identity, range(self.owner.index.ordered_group_count(self.stream, identity))
        )

    def __len__(self):
        self.owner._usable()
        return self.owner.index.count(self.stream + ":keys")

    def __iter__(self):
        self.owner._usable()
        for raw in self.owner.index.rows(self.stream + ":keys"):
            yield tuple(json.loads(raw))


class NextMarkers:
    def __init__(self, markers):
        if type(markers) is not PublicUnique or (markers._rows._role, markers._rows._field) != (
            "measurements",
            "markers",
        ):
            raise TypeError("actual indexed markers required")
        self.owner = markers._rows._owner
        self.owner._usable()
        self.owner.budget.reserve(1024, rows=1)
        self.stream = "public-next-marker:" + str(self.owner._relation_serial)
        self.owner._relation_serial += 1

    def __setitem__(self, key, value):
        self.owner._usable()
        if type(key) is not str or type(value) is not int or value < 0:
            raise ValueError("original marker next timestamp required")
        self.owner.budget.reserve(len(key) * 12 + 256, rows=1)
        self.owner.index.group(
            self.stream, json.dumps(key, ensure_ascii=True), json.dumps(value).encode()
        )

    def get(self, key):
        self.owner._usable()
        if type(key) is not str:
            return None
        self.owner.budget.reserve(len(key) * 12 + 128, rows=0)
        raw = self.owner.index.group_last(self.stream, json.dumps(key, ensure_ascii=True))
        return None if raw is None else json.loads(raw)
