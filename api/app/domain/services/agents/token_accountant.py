#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app.domain.external.observability import ObservabilityPort
from app.domain.models.event import UsageEvent
from app.domain.models.llm_token_usage import LLMTokenUsage
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class TokenAccountant:
    """记录 LLM token 用量，并在内存中维护会话级增量汇总。"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            session_id: str,
            agent_name: str,
            model_name: str,
            model_id: Optional[str] = None,
            observability_port: Optional[ObservabilityPort] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._agent_name = agent_name
        self._model_name = model_name
        self._model_id = model_id
        self._prompt_tokens: Optional[int] = None
        self._completion_tokens: Optional[int] = None
        self._total_tokens: Optional[int] = None
        self._call_count: Optional[int] = None
        self._model_price_per_million: Optional[tuple[float, float]] = None
        self._pending_records: List[LLMTokenUsage] = []
        self._flush_batch_size = 5
        if observability_port is None:
            raise ValueError("TokenAccountant requires observability_port")
        self._observability = observability_port

    def sync_model(self, model_id: Optional[str], model_name: str) -> None:
        """Update the active model used for subsequent token records (e.g. after fallback)."""
        if model_id != self._model_id or model_name != self._model_name:
            self._model_id = model_id
            self._model_name = model_name
            self._model_price_per_million = None

    async def _resolve_model_id(self, model_id: Optional[str]) -> Optional[str]:
        if not model_id:
            return None
        async with self._uow_factory() as uow:
            model = await uow.llm_model.get_by_id(model_id)
        if model is None:
            logger.warning("Token usage model_id=%s not found in llm_models; recording without model_id", model_id)
            return None
        return model_id

    async def flush(self) -> None:
        if not self._pending_records:
            return
        records = self._pending_records
        self._pending_records = []
        try:
            async with self._uow_factory() as uow:
                session = await uow.session.get_metadata(self._session_id)
                if session:
                    for record in records:
                        record.owner_user_id = session.owner_user_id
                        record.team_id = session.team_id
                for record in records:
                    record.model_id = await self._resolve_model_id(record.model_id)
                await uow.llm_token_usage.save_many(records)
        except IntegrityError as exc:
            logger.warning("写入 token 用量失败（已忽略，不影响任务）: %s", exc)

    async def record(self, usage: Dict[str, int], step: str) -> Optional[UsageEvent]:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cached_tokens = int(usage.get("cached_tokens") or 0)
        cache_write_tokens = int(usage.get("cache_write_tokens") or 0)
        if prompt_tokens <= 0 and completion_tokens <= 0 and cached_tokens <= 0 and cache_write_tokens <= 0:
            return None

        self._observability.record_llm_tokens(
            self._model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )
        model_id = await self._resolve_model_id(self._model_id)
        record = LLMTokenUsage(
            session_id=self._session_id,
            agent=self._agent_name,
            step=step,
            model_id=model_id,
            model_name=self._model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage.get("total_tokens") or (prompt_tokens + completion_tokens)),
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_metric_source=str(usage.get("cache_metric_source") or ""),
        )

        self._pending_records.append(record)
        if self._call_count is None:
            self._prompt_tokens = prompt_tokens
            self._completion_tokens = completion_tokens
            self._total_tokens = record.total_tokens
            self._call_count = 1
        else:
            self._prompt_tokens = (self._prompt_tokens or 0) + prompt_tokens
            self._completion_tokens = (self._completion_tokens or 0) + completion_tokens
            self._total_tokens = (self._total_tokens or 0) + record.total_tokens
            self._call_count = (self._call_count or 0) + 1

        if len(self._pending_records) >= self._flush_batch_size:
            await self.flush()

        cost = await self._estimate_cost()
        return UsageEvent(
            prompt_tokens=self._prompt_tokens or 0,
            completion_tokens=self._completion_tokens or 0,
            total_tokens=self._total_tokens or 0,
            estimated_cost_usd=round(cost, 6),
            call_count=self._call_count or 0,
            delta_prompt_tokens=prompt_tokens,
            delta_completion_tokens=completion_tokens,
        )

    async def _estimate_cost(self) -> float:
        if not self._model_id:
            return 0.0
        if self._model_price_per_million is None:
            async with self._uow_factory() as uow:
                model = await uow.llm_model.get_by_id(self._model_id)
            self._model_price_per_million = (
                (model.input_price_per_million, model.output_price_per_million)
                if model else (0.0, 0.0)
            )
        input_price, output_price = self._model_price_per_million
        return (
            (self._prompt_tokens or 0) * input_price / 1_000_000
            + (self._completion_tokens or 0) * output_price / 1_000_000
        )
