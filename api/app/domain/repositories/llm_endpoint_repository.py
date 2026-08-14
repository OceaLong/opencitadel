#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.scope import OwnerScope


class LLMEndpointRepository(ABC):
    @abstractmethod
    async def get_all(self, scope: Optional[OwnerScope] = None) -> List[LLMEndpoint]:
        ...

    @abstractmethod
    async def list_hosts(self, scope: Optional[OwnerScope] = None) -> List[str]:
        """List lowercase hostnames parsed from configured endpoint base_urls,
        without decrypting API keys (least-privilege read for compliance checks)."""
        ...

    @abstractmethod
    async def get_by_id(self, endpoint_id: str, scope: Optional[OwnerScope] = None) -> Optional[LLMEndpoint]:
        ...

    @abstractmethod
    async def save(self, endpoint: LLMEndpoint, encrypted_api_key: str) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, endpoint_id: str) -> None:
        ...

    @abstractmethod
    async def count_models(self, endpoint_id: str) -> int:
        ...
