#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional, Protocol

from app.domain.models.fortune_prediction import FortunePrediction


class FortunePredictionRepository(Protocol):
    async def save(self, prediction: FortunePrediction) -> None:
        ...

    async def get_by_share_id(self, share_id: str) -> Optional[FortunePrediction]:
        ...
