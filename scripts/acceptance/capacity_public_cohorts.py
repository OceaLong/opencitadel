"""Complete bounded-row cohort relation for base plus every physical round."""

import hashlib
import json

from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import Cohort, Counts, RoundOrigin, SourceOrigin
from scripts.acceptance.capacity_package import PublicRows


def cohort_bytes(owner, cohort):
    if type(cohort) is not Cohort:
        raise TypeError("actual original cohort required")

    # Leaf was originally admitted under a full shard bound. Charge another
    # complete ID/control export before making its bounded JSON model copy.
    def precharge(value):
        if type(value) is str:
            owner.budget.reserve(len(value) * 16 + 256, rows=0)
        elif type(value) is list:
            owner.budget.reserve(len(value) * 16 + 256, rows=0)
            for item in value:
                precharge(item)
        elif type(value) in (Cohort, Counts, SourceOrigin, RoundOrigin):
            owner.budget.reserve(len(type(value).model_fields) * 256, rows=0)
            for name in type(value).model_fields:
                precharge(getattr(value, name))
        elif value is None or type(value) in (int, bool):
            owner.budget.reserve(256, rows=0)
        else:
            raise TypeError("closed original cohort field required")

    precharge(cohort)
    return json.dumps(
        cohort.model_dump(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def base_cohort_digest(rows):
    if type(rows) is not PublicRows or (rows._role, rows._field) != ("seal", "cohorts"):
        raise TypeError("actual base cohort rows required")
    digest = hashlib.sha256(b"[")
    separator = b""
    for cohort in rows:
        digest.update(separator)
        digest.update(cohort_bytes(rows._owner, cohort))
        separator = b","
    digest.update(b"]")
    return digest.hexdigest()


class CohortJoin:
    def __init__(self, owner):
        owner._usable()
        self.owner = owner
        self.duplicate = False
        owner.budget.reserve(1024, rows=1)
        self.stream = "public-cohorts:" + str(owner._relation_serial)
        owner._relation_serial += 1

    def extend(self, rows):
        self.owner._usable()
        for cohort in rows:
            raw = cohort_bytes(self.owner, cohort)
            self.owner.budget.reserve(len(cohort.cohort_id) * 12 + 256, rows=1)
            key = json.dumps(cohort.cohort_id)
            if self.owner.index.find(self.stream, "identity", key) is not None:
                self.duplicate = True
            else:
                self.owner.index.append(self.stream, raw, keys={"identity": key})

    def finish(self):
        self.owner._usable()
        if self.duplicate:
            raise ValueError("duplicate cohort_id")
        return self

    def values(self):
        self.finish()
        for raw in self.owner.index.rows(self.stream):
            self.owner.budget.reserve(len(raw) * 64, rows=1)
            yield Cohort.model_validate(strict_json(raw))

    def __len__(self):
        self.finish()
        return self.owner.index.count(self.stream)

    def matches(self, actual):
        self.finish()
        equal = len(self) == len(actual)
        for row in self.values():
            if row.cohort_id not in actual or actual[row.cohort_id] != row:
                equal = False
        return equal
