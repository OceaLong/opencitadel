#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional

from app.domain.models.oauth_identity import OAuthIdentity


class OAuthIdentityRepository(ABC):
    @abstractmethod
    async def get_by_provider_identity(self, provider: str, provider_user_id: str) -> Optional[OAuthIdentity]:
        ...

    @abstractmethod
    async def save(self, identity: OAuthIdentity) -> None:
        ...
