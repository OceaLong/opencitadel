"""Exact model catalog for the four greenfield bounded contexts."""

from importlib import import_module

from app.contexts.database import GreenfieldBase as Base

MODEL_MODULES = (
    "app.contexts.identity.models",
    "app.contexts.inference.models",
    "app.contexts.knowledge.models",
    "app.kernel.infrastructure.postgres.models",
)

for module_name in MODEL_MODULES:
    import_module(module_name)

model_metadata = Base.metadata

__all__ = ["MODEL_MODULES", "model_metadata"]
