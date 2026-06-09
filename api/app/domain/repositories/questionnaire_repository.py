#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional, Protocol

from app.domain.models.questionnaire import Questionnaire, QuestionnaireResponse


class QuestionnaireRepository(Protocol):
    async def save(self, questionnaire: Questionnaire) -> None:
        ...

    async def get_by_id(self, questionnaire_id: str) -> Optional[Questionnaire]:
        ...

    async def get_by_slug(self, slug: str) -> Optional[Questionnaire]:
        ...

    async def save_response(self, response: QuestionnaireResponse) -> None:
        ...

    async def count_responses(self, questionnaire_id: str) -> int:
        ...

    async def list_responses(
            self,
            questionnaire_id: str,
            *,
            limit: int = 5000,
    ) -> List[QuestionnaireResponse]:
        ...
