import logging
from collections.abc import Callable

from app.domain.models.llm_token_usage import LLMTokenUsage, SessionTokenUsageSummary
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class LLMTokenUsageService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def record(
        self,
        session_id: str,
        agent: str,
        step: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        model_id: str | None = None,
        call_type: str = "invoke",
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_metric_source: str = "",
        owner_user_id: str | None = None,
        team_id: str | None = None,
    ) -> LLMTokenUsage:
        usage = LLMTokenUsage(
            session_id=session_id,
            agent=agent,
            step=step or "default",
            model_id=model_id,
            model_name=model_name,
            call_type=call_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_metric_source=cache_metric_source,
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        async with self._uow_factory() as uow:
            await uow.llm_token_usage.save(usage)
            await uow.commit()
        return usage

    async def list_by_session(self, session_id: str) -> list[LLMTokenUsage]:
        async with self._uow_factory() as uow:
            return await uow.llm_token_usage.list_by_session(session_id)

    async def get_session_summary(
        self,
        session_id: str,
        model_prices: dict[str, tuple[float, float]] | None = None,
    ) -> SessionTokenUsageSummary:
        async with self._uow_factory() as uow:
            summary = await uow.llm_token_usage.aggregate_by_session(session_id)
            if not model_prices:
                return summary
            records = await uow.llm_token_usage.list_by_session(session_id)
        cost = 0.0
        for record in records:
            key = record.model_id or record.model_name
            prices = model_prices.get(key) or model_prices.get(record.model_name)
            if not prices:
                continue
            input_price, output_price = prices
            cost += (
                record.prompt_tokens * input_price / 1_000_000
                + record.completion_tokens * output_price / 1_000_000
            )
        summary.estimated_cost_usd = round(cost, 6)
        return summary
