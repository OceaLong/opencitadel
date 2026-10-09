"""Exact string sets for fixed public progress identities, in the same index."""

import hashlib
import json

from scripts.acceptance.capacity_package import PackageSession, PublicUnique


class PublicIDs:
    def __init__(self, owner, family, values=()):
        if type(owner) is not PackageSession or family not in {
            "eligible-progress",
            "paint-progress",
            "all-progress",
            "ack-progress",
            "cohort-runs",
            "physical-children",
            "physical-boots",
            "physical-clones",
            "retained-scopes",
            "provider-calls",
            "projection-origins",
            "used-live-runs",
            "used-live-sessions",
            "used-live-batches",
            "retained-live-runs",
            "retained-admission-runs",
        }:
            raise TypeError("fixed public identity set required")
        owner._usable()
        self.owner, self.family = owner, family
        owner.budget.reserve(1024, rows=1)
        self.stream = "public-ids:" + str(owner._relation_serial)
        owner._relation_serial += 1
        self.update(values)

    def _key(self, value):
        self.owner._usable()
        if type(value) is not str:
            raise TypeError("original public string identity required")
        self.owner.budget.reserve(len(value) * 12 + 128, rows=0)
        return json.dumps(value, ensure_ascii=True)

    def add(self, value):
        key = self._key(value)
        if self.owner.index.find(self.stream, "identity", key) is None:
            self.owner.budget.reserve(len(key) + 256, rows=1)
            from scripts.execution_capacity.original_dictionaries import key_identity

            ordered = key_identity(self.owner, value)
            self.owner.index.append(
                self.stream, key.encode(), keys={"identity": key, "ordered": ordered}
            )

    def update(self, values):
        for value in values:
            self.add(value)

    def __len__(self):
        self.owner._usable()
        return self.owner.index.count(self.stream)

    def __contains__(self, value):
        self.owner._usable()
        if type(value) is not str:
            return False
        return self.owner.index.find(self.stream, "identity", self._key(value)) is not None

    def __iter__(self):
        self.owner._usable()
        for raw in self.owner.index.rows(self.stream):
            yield json.loads(raw)

    def _other(self, other):
        self.owner._usable()
        if type(other) is PublicIDs:
            other.owner._usable()
            if other.owner is not self.owner:
                raise ValueError("foreign public identity set")
        elif type(other) is PublicUnique:
            other._rows._owner._usable()
            if other._rows._owner is not self.owner:
                raise ValueError("foreign public identity lookup")
        elif type(other) is not set:
            raise TypeError("closed public identity comparison required")
        return other

    def __le__(self, other):
        other = self._other(other)
        valid = True
        for value in self:
            if value not in other:
                valid = False
        return valid

    def __eq__(self, other):
        other = self._other(other)
        valid = len(self) == len(other)
        for value in self:
            if value not in other:
                valid = False
        return valid

    def __or__(self, other):
        other = self._other(other)
        result = PublicIDs(self.owner, self.family, self)
        result.update(other)
        return result

    def intersects(self, other):
        other = self._other(other)
        found = False
        for value in self:
            if value in other:
                found = True
        return found

    def sorted_digest(self):
        self.owner._usable()
        digest = hashlib.sha256(b"[")
        separator = b""
        for raw in self.owner.index.identity_rows(self.stream, "ordered"):
            digest.update(separator)
            digest.update(raw)
            separator = b","
        digest.update(b"]")
        return digest.hexdigest()
