"""Thirty-second caller-bound metrics with current authorization after every read."""

from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime
from time import monotonic

from app.application.ports.execution_analysis import AnalysisQuery, CoverageChanged, MetricEnvelope


class ExecutionAnalysisService:
    def __init__(self, repository, principal, *, workspace_timezone=None, clock=monotonic):
        self.repository, self.principal = repository, principal.model_copy(deep=True)
        self.workspace_timezone, self.clock = workspace_timezone, clock
        self.cache = OrderedDict()
        self.lookup = {}

    async def summary(self, scope, filters, grain, timezone, watermark=None):
        if scope.user_id != self.principal.user_id:
            raise PermissionError("analysis_scope_mismatch")
        # Stable default range within the cache TTL. Explicit ranges remain exact.
        now = datetime.fromtimestamp(int(datetime.now(UTC).timestamp() // 30) * 30, UTC)
        query = AnalysisQuery.parse(
            filters, grain, timezone, workspace_timezone=self.workspace_timezone, now=now
        )
        key = (scope.model_dump_json(), self.principal.model_dump_json(), query, watermark)
        cache_key = self.lookup.get(key)
        cached = self.cache.get(cache_key)
        if cached and self.clock() - cached[0] < 30:
            capture = cached[1]
        else:
            capture = await self.repository.capture(scope, self.principal, query, watermark)
        current = await self.repository.current(scope, self.principal, capture)
        if current != capture.authority:
            self.cache.pop(cache_key, None)
            self.lookup.pop(key, None)
            raise CoverageChanged()
        # The fresh current check is the response authorization linearization point.
        if not cached or cached[1] is not capture:
            cache_key = (key, capture.authority)
            self.lookup[key] = cache_key
            self.cache[cache_key] = (self.clock(), capture)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 32:
                old_key, _ = self.cache.popitem(last=False)
                if self.lookup.get(old_key[0]) == old_key:
                    self.lookup.pop(old_key[0], None)
        return MetricEnvelope(
            deepcopy(capture.metrics), query.grain, query.timezone, capture.watermark
        )

    async def runs(self, scope, filters, grain, timezone, watermark, *, cursor=None, limit=50):
        if not watermark or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_analysis_page")
        if scope.user_id != self.principal.user_id:
            raise PermissionError("analysis_scope_mismatch")
        query = AnalysisQuery.parse(
            filters, grain, timezone, workspace_timezone=self.workspace_timezone
        )
        capture = await self.repository.capture(scope, self.principal, query, watermark)
        if await self.repository.current(scope, self.principal, capture) != capture.authority:
            raise CoverageChanged()
        page = await self.repository.run_page(
            scope, self.principal, capture, cursor=cursor, limit=limit
        )
        if await self.repository.current(scope, self.principal, capture) != capture.authority:
            raise CoverageChanged()
        return page
