#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight LLM-based skill recommendation."""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from pydantic import BaseModel, Field

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.skill import Skill, SkillRecommendResult

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = 0.55


class _RecommendSchema(BaseModel):
    skill_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""

    model_config = {"extra": "ignore"}


class SkillRecommenderService:
    def __init__(
            self,
            llm: LLM,
            json_parser: JSONParser,
            *,
            confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._llm = llm
        self._json_parser = json_parser
        self._confidence_threshold = confidence_threshold

    async def recommend(
            self,
            message: str,
            candidates: List[Skill],
    ) -> SkillRecommendResult:
        eligible = [s for s in candidates if s.enabled and s.auto_recommend]
        if not message.strip() or not eligible:
            return SkillRecommendResult()

        catalog_lines = []
        for skill in eligible:
            catalog_lines.append(
                f"- id={skill.id} | {skill.name}: {skill.description}"
            )
            if skill.examples:
                catalog_lines.append(f"  examples: {'; '.join(skill.examples[:3])}")

        prompt = (
            "Select at most one skill id that best matches the user message, "
            "or return null skill_id if none fit.\n"
            "Return JSON: {skill_id, confidence (0-1), reason}.\n\n"
            f"User message:\n{message.strip()}\n\n"
            f"Candidates:\n" + "\n".join(catalog_lines)
        )
        try:
            response = await self._llm.invoke(
                [{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            content = response.get("content") or ""
            if not content.strip():
                return SkillRecommendResult()
            raw = await self._json_parser.invoke(content)
            if isinstance(raw, str):
                import json
                raw = json.loads(raw)
            parsed = _RecommendSchema.model_validate(raw)
            if not parsed.skill_id or parsed.confidence < self._confidence_threshold:
                return SkillRecommendResult(reason=parsed.reason)
            if not any(s.id == parsed.skill_id for s in eligible):
                return SkillRecommendResult(reason="Recommended skill not in candidate set")
            return SkillRecommendResult(
                skill_id=parsed.skill_id,
                confidence=parsed.confidence,
                reason=parsed.reason,
            )
        except Exception as exc:
            logger.warning("Skill recommendation failed: %s", exc)
            return SkillRecommendResult(reason=str(exc))


def build_skill_recommender(
        llm_factory: Callable[[], LLM],
        json_parser: JSONParser,
) -> SkillRecommenderService:
    return SkillRecommenderService(llm_factory(), json_parser)
