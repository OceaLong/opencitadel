#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Domain-level port for runtime application configuration.

Domain services must not depend on the application layer directly (that
would invert the dependency direction). This module defines a small
provider-registration port: the application layer registers its concrete
`get_runtime_config` implementation here at import time, and domain code
depends only on this module.

Fails fast (raises RuntimeError) if no provider has been registered yet,
rather than silently falling back to defaults.
"""
from typing import Callable, Optional

from app.domain.models.app_config import AppConfig

_provider: Optional[Callable[[], AppConfig]] = None


def register_runtime_config_provider(provider: Callable[[], AppConfig]) -> None:
    """Register the concrete runtime-config accessor. Last registration wins."""
    global _provider
    _provider = provider


def get_runtime_config() -> AppConfig:
    """Return the current runtime AppConfig via the registered provider.

    Raises:
        RuntimeError: if no provider has been registered yet.
    """
    if _provider is None:
        raise RuntimeError(
            "Runtime config provider not registered. "
            "Ensure app.application.services.config_provider has been imported."
        )
    return _provider()
