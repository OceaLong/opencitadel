from datetime import datetime

from app.application.ports.queries import (
    UsageBreakdownDimension,
    UsageQueryPort,
)


class UsageStatsService:
    def __init__(self, query: UsageQueryPort) -> None:
        self._query = query

    async def aggregate_usage(
        self,
        *,
        owner_user_id: str | None = None,
        team_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict:
        result = await self._query.aggregate(
            owner_user_id=owner_user_id,
            team_id=team_id,
            start_at=start_at,
            end_at=end_at,
        )
        return {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "cached_tokens": result.cached_tokens,
            "call_count": result.call_count,
        }

    async def usage_timeseries(
        self,
        *,
        owner_user_id: str | None = None,
        team_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[dict]:
        rows = await self._query.timeseries(
            owner_user_id=owner_user_id,
            team_id=team_id,
            start_at=start_at,
            end_at=end_at,
        )
        return [
            {
                "date": row.date,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "total_tokens": row.total_tokens,
                "cached_tokens": row.cached_tokens,
                "call_count": row.call_count,
            }
            for row in rows
        ]

    async def usage_breakdown(
        self,
        *,
        dimension: UsageBreakdownDimension,
        owner_user_id: str | None = None,
        team_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 10,
    ) -> list[dict]:
        rows = await self._query.breakdown(
            dimension=dimension,
            owner_user_id=owner_user_id,
            team_id=team_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
        return [
            {
                "key": row.key,
                "total_tokens": row.total_tokens,
                "call_count": row.call_count,
            }
            for row in rows
        ]


__all__ = ["UsageBreakdownDimension", "UsageStatsService"]
