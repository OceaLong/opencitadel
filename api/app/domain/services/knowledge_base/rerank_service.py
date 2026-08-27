"""Optional reranking for KB retrieval candidates."""

import asyncio
import json
import logging
import time
from typing import TypeVar

from app.domain.external.llm import LLM
from app.domain.runtime_policy import KnowledgeRerankPolicy

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RerankService:
    def __init__(self, llm: LLM | None, *, policy: KnowledgeRerankPolicy) -> None:
        self._llm = llm
        self._policy = policy

    async def rerank(self, query: str, candidates: list[T], top_n: int) -> list[T]:
        if not self._policy.enabled or not self._llm or len(candidates) <= 1:
            return candidates[:top_n]
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._rerank_with_llm(query, candidates, top_n),
                timeout=self._policy.timeout_seconds,
            )
            logger.info(
                "kb_rerank completed candidates=%s top_n=%s duration_ms=%.1f",
                len(candidates),
                top_n,
                (time.perf_counter() - started) * 1000,
            )
            return result
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "kb_rerank fallback candidates=%s top_n=%s duration_ms=%.1f error=%s",
                len(candidates),
                top_n,
                (time.perf_counter() - started) * 1000,
                exc,
            )
            return candidates[:top_n]

    async def _rerank_with_llm(self, query: str, candidates: list[T], top_n: int) -> list[T]:
        snippets = []
        for idx, candidate in enumerate(candidates):
            content = getattr(candidate, "content", "")
            snippets.append(f"[{idx}] {content[:800]}")
        prompt = (
            "请根据用户问题对候选片段相关性排序，只返回 JSON 数组，数组元素是候选编号，"
            "最相关在前，不要输出解释。\n\n"
            f"用户问题: {query}\n\n候选片段:\n" + "\n\n".join(snippets)
        )
        response = await self._llm.invoke(messages=[{"role": "user", "content": prompt}])
        text = str(response.get("content") or response.get("reasoning_content") or "[]").strip()
        order = json.loads(text)
        if not isinstance(order, list):
            return candidates[:top_n]
        ranked = []
        seen = set()
        for item in order:
            try:
                idx = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(candidates) and idx not in seen:
                ranked.append(candidates[idx])
                seen.add(idx)
        for idx, candidate in enumerate(candidates):
            if idx not in seen:
                ranked.append(candidate)
        return ranked[:top_n]
