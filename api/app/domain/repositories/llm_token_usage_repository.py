from abc import ABC, abstractmethod

from app.domain.models.llm_token_usage import LLMTokenUsage, SessionTokenUsageSummary


class LLMTokenUsageRepository(ABC):
    @abstractmethod
    async def save(self, usage: LLMTokenUsage) -> None: ...

    @abstractmethod
    async def save_many(self, usages: list[LLMTokenUsage]) -> None: ...

    @abstractmethod
    async def list_by_session(self, session_id: str) -> list[LLMTokenUsage]: ...

    @abstractmethod
    async def aggregate_by_session(self, session_id: str) -> SessionTokenUsageSummary: ...
