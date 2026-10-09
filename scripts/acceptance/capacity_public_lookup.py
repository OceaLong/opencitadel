"""Fixed complete-row locators preserving original dict overwrite/order semantics."""

import json

from scripts.acceptance.capacity_package import PublicRows, PublicUnique


class PublicLookup:
    def __init__(self, records, mode):
        if type(records) is PublicUnique:
            records = records._rows
        expected = {
            "round-window": ("cleanup", "rounds"),
            "round-sample": ("cleanup", "rounds"),
            "provider-calls": ("cleanup", "dispositions"),
            "calibration-phase": ("network", "phase_intervals"),
        }
        if type(records) is not PublicRows or expected.get(mode) != (records._role, records._field):
            raise TypeError("fixed original public relation required")
        self.rows, self.owner, self.mode = records, records._owner, mode
        self.owner._usable()
        self.owner.budget.reserve(1024, rows=1)
        self.stream = "public-lookup:" + str(self.owner._relation_serial)
        self.owner._relation_serial += 1
        for position, row in enumerate(records):
            key = self._identity(row)
            if key is None:
                continue
            encoded = self._encode(key)
            self.owner.budget.reserve(len(encoded) * 2 + 256, rows=1)
            if self.owner.index.find(self.stream, "key", encoded) is None:
                self.owner.index.append(self.stream, encoded.encode(), keys={"key": encoded})
            self.owner.index.group(self.stream, encoded, str(position).encode())

    def _identity(self, row):
        if self.mode == "calibration-phase":
            return row.window_id, row.phase
        if self.mode == "provider-calls":
            return row.resource_id if row.kind == "provider_call" else None
        return (
            row.origin.round.window_id
            if self.mode == "round-window"
            else row.origin.round.sample_id
        )

    def _encode(self, key):
        self.owner._usable()
        if self.mode == "calibration-phase":
            if (
                type(key) is not tuple
                or len(key) != 2
                or any(type(item) is not str for item in key)
            ):
                raise KeyError(key)
            size = sum(len(item) for item in key)
        else:
            if type(key) is not str:
                raise KeyError(key)
            size = len(key)
        self.owner.budget.reserve(size * 12 + 128, rows=0)
        return json.dumps(key, ensure_ascii=True)

    def __len__(self):
        self.owner._usable()
        return self.owner.index.count(self.stream)

    def __iter__(self):
        self.owner._usable()
        for raw in self.owner.index.rows(self.stream):
            value = json.loads(raw)
            yield tuple(value) if self.mode == "calibration-phase" else value

    def __getitem__(self, key):
        raw = self.owner.index.group_last(self.stream, self._encode(key))
        if raw is None:
            raise KeyError(key)
        row = self.rows[int(raw)]
        if self._identity(row) != key:
            raise ValueError("original public relationship changed")
        return row

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def items(self):
        for key in self:
            yield key, self[key]

    def values(self):
        for key in self:
            yield self[key]
