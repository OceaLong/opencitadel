"""Evidence-owned reads over the existing bounded object transport."""

import time

from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError


class EvidenceObjects:
    def __init__(self, delegate, budget, *, object_limit=2 * 1024 * 1024, evidence=None):
        if type(object_limit) is not int or not 1 <= object_limit <= 2 * 1024 * 1024:
            raise ValueError("invalid private object quota")
        self.delegate, self.budget, self.object_limit = delegate, budget, object_limit
        self.journal = None if evidence is None else evidence.journal
        self.originals = [] if self.journal is None else self.journal.sequence("objects")

    async def get_bytes(self, key):
        return (await self.get_bounded_bytes(key, self.object_limit)).data

    async def get_bounded_bytes(self, key, limit):
        if type(limit) is not int or not 1 <= limit <= self.object_limit:
            raise ValueError("invalid private object read limit")
        # Reserve bounded transport chunks + join/slice + original retention
        # before calling the existing adapter. Unused allowance is conservative.
        self.budget.reserve(4 * (limit + 1), rows=1)
        self.budget.charge({"key": key})
        record = {"key": key, "start_ns": time.monotonic_ns(), "data": None, "error": None}
        token = None
        if self.journal is None:
            self.originals.append(record)
        else:
            token = self.journal.begin("objects", record)
        try:
            if self.journal is None:
                value = await self.delegate.get_bounded_bytes(key, limit)
            else:

                def observed(offset, chunk):
                    self.journal.note(token, {"offset": offset, "data": chunk})

                value = await self.delegate.get_bounded_bytes(key, limit, observed=observed)
            if type(value.data) is not bytes or len(value.data) > limit or value.truncated:
                raise EvidenceQuotaError("private object input quota exceeded")
            record["data"] = value.data
            # All capacity object consumers may parse JSON after this return.
            # Reserve a conservative decoded node/copy allowance first.
            self.budget.reserve(len(value.data) * 64, rows=1)
            return value
        except BaseException as error:
            record["error"] = type(error).__name__
            raise
        finally:
            record["end_ns"] = time.monotonic_ns()
            if self.journal is not None:
                self.journal.complete(token, record)
