#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import TypeVar

from .checkpoint_repository import CheckpointRepository
from .file_repository import FileRepository
from .llm_model_repository import LLMModelRepository
from .llm_token_usage_repository import LLMTokenUsageRepository
from .memory_entry_repository import MemoryEntryRepository
from .session_repository import SessionRepository
from .skill_repository import SkillRepository

T = TypeVar("T", bound="IUnitOfWork")


class IUnitOfWork(ABC):
    """Uow模式协议接口"""
    checkpoint: CheckpointRepository
    file: FileRepository
    session: SessionRepository
    llm_model: LLMModelRepository
    skill: SkillRepository
    memory_entry: MemoryEntryRepository
    llm_token_usage: LLMTokenUsageRepository

    @abstractmethod
    async def commit(self):
        """提交数据库数据持久化"""
        ...

    @abstractmethod
    async def rollback(self):
        """数据库回滚"""
        ...

    @abstractmethod
    async def __aenter__(self: T) -> T:
        """进入上下文管理器"""
        ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        ...
