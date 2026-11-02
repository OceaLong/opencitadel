"""One shared object-read allowance for a context preparation invocation."""

from contextvars import ContextVar
from dataclasses import dataclass, field

from app.domain.external.object_storage import BoundedObjectBytes

_active = ContextVar("context_prepare_read_budget", default=None)


@dataclass
class ContextReadBudget:
    remaining: int
    consumed: int = 0
    objects: dict = field(default_factory=dict, repr=False)

    def __enter__(self):
        if _active.get() is not None or not 1 <= self.remaining <= 8388608:
            raise ValueError("CONTEXT_READ_BUDGET_INVALID")
        self._token = _active.set(self)
        return self

    def __exit__(self, *_args):
        _active.reset(self._token)


async def read_bounded(storage, key, limit):
    budget = _active.get()
    if budget is None:
        return await storage.get_bounded_bytes(key, limit)
    cached = budget.objects.get((id(storage), key))
    if cached is not None:
        # Only immutable content bytes are memoized within this invocation.
        # Every caller still checks its formal source, digest and current grant.
        return BoundedObjectBytes(data=cached[:limit], truncated=len(cached) > limit)
    # Reserve before awaiting so parallel children cannot overspend. Bounded
    # storage may read one extra byte to establish its truncated flag.
    allowance = min(limit, budget.remaining - 1)
    if allowance < 1:
        raise ValueError("CONTEXT_PREPARE_READ_BUDGET_EXCEEDED")
    reserved = allowance + 1
    budget.remaining -= reserved
    budget.consumed += reserved
    result = await storage.get_bounded_bytes(key, allowance)
    actual = len(result.data) + int(result.truncated)
    if actual > reserved:
        raise ValueError("CONTEXT_READ_LIMIT_INVALID")
    budget.remaining += reserved - actual
    budget.consumed -= reserved - actual
    if result.truncated and allowance < limit:
        raise ValueError("CONTEXT_PREPARE_READ_BUDGET_EXCEEDED")
    if not result.truncated:
        budget.objects[(id(storage), key)] = result.data
    return result
