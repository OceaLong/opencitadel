"""Explicit full model registry used only by Alembic metadata discovery."""

from importlib import import_module

from app.infrastructure.models.base import Base

MODEL_MODULES = (
    "app.infrastructure.execution.models",
    "app.infrastructure.models.audit_log",
    "app.infrastructure.models.codebase",
    "app.infrastructure.models.codebase_version",
    "app.infrastructure.models.delivery_artifact",
    "app.infrastructure.models.file",
    "app.infrastructure.models.integration_server",
    "app.infrastructure.models.invitation",
    "app.infrastructure.models.knowledge_base",
    "app.infrastructure.models.knowledge_version",
    "app.infrastructure.models.inference_binding",
    "app.infrastructure.models.inference_endpoint",
    "app.infrastructure.models.inference_model",
    "app.infrastructure.models.llm_token_usage",
    "app.infrastructure.models.memory_entry",
    "app.infrastructure.models.notification",
    "app.infrastructure.models.oauth_identity",
    "app.infrastructure.models.patrol",
    "app.infrastructure.models.refresh_token",
    "app.infrastructure.models.runtime_policy",
    "app.infrastructure.models.scheduled_job",
    "app.infrastructure.models.service_api_key",
    "app.infrastructure.models.session",
    "app.infrastructure.models.session_file_attachment",
    "app.infrastructure.models.session_resource_binding",
    "app.infrastructure.models.skill",
    "app.infrastructure.models.team",
    "app.infrastructure.models.user",
    "app.infrastructure.models.user_quota",
)

for module_name in MODEL_MODULES:
    import_module(module_name)

model_metadata = Base.metadata

__all__ = ["MODEL_MODULES", "model_metadata"]
