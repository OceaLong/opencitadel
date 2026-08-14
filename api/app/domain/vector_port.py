#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Domain-level port for vector-memory embedding access.

Domain services must not depend on the application layer directly (that
would invert the dependency direction). This module defines a small
provider-registration port: the application layer registers its concrete
`VectorMemoryService` singleton here at import time, and domain code
depends only on this module.

Fails fast (raises RuntimeError) if no provider has been registered yet,
rather than silently falling back to a no-op embedder.
"""
from typing import Callable, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class VectorMemoryPort(Protocol):
    """The subset of VectorMemoryService that domain vector wrappers use."""

    enabled: bool

    async def embed(self, content: str) -> List[float]:
        ...

    async def embed_batch(self, contents: List[str]) -> List[List[float]]:
        ...

    async def embed_batch_unconditional(self, contents: List[str]) -> List[List[float]]:
        ...


_provider: Optional[Callable[[], VectorMemoryPort]] = None


def register_vector_memory_provider(provider: Callable[[], VectorMemoryPort]) -> None:
    """Register the concrete vector-memory accessor. Last registration wins."""
    global _provider
    _provider = provider


def get_vector_memory() -> VectorMemoryPort:
    """Return the current VectorMemoryPort via the registered provider.

    Raises:
        RuntimeError: if no provider has been registered yet.
    """
    if _provider is None:
        raise RuntimeError(
            "Vector memory provider not registered. "
            "Ensure app.application.services.vector_memory_service has been imported."
        )
    return _provider()
