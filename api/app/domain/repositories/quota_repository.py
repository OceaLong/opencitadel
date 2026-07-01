#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional

from app.domain.models.user_quota import UserQuota


class QuotaRepository(ABC):
    @abstractmethod
    async def get_for_user(self, user_id: str) -> Optional[UserQuota]:
        ...

    @abstractmethod
    async def save(self, quota: UserQuota) -> None:
        ...
