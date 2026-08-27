"""Environment-only Actuator configuration; credentials never leave this process.

Structure mirrors ops-collector/src/opencitadel_ops_collector/config.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkloadTarget(BaseModel):
    kind: Literal["deployment", "statefulset"]
    min_replicas: int = Field(default=0, ge=0)
    max_replicas: int = Field(ge=0)


class ActuatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPS_ACTUATOR_", env_nested_delimiter="__")

    target_ref: str = "opencitadel-local"
    allowed_namespaces: list[str] = Field(default_factory=lambda: ["opencitadel"])
    allowed_workloads: dict[str, dict[str, WorkloadTarget]] = Field(default_factory=dict)
    transport: Literal["streamable-http", "stdio"] = "streamable-http"
    allow_stdio: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8091, ge=1, le=65535)
    max_output_bytes: int = Field(default=65536, ge=1024, le=1048576)
    max_rows: int = Field(default=200, ge=1, le=1000)
    max_array_items: int = Field(default=200, ge=1, le=1000)
    max_string_chars: int = Field(default=32768, ge=256, le=131072)
    concurrency: int = Field(default=4, ge=1, le=8)

    @field_validator("allowed_namespaces")
    @classmethod
    def namespaces_are_unique(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned or len(cleaned) != len(set(cleaned)):
            raise ValueError("allowed_namespaces must be non-empty and unique")
        return cleaned
