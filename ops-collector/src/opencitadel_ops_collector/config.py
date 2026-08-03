"""Environment-only Collector configuration; credentials never leave this process."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HttpTarget(BaseModel):
    url: str
    expected_statuses: list[int] = Field(default_factory=lambda: list(range(200, 300)), max_length=20)
    timeout_seconds: float = Field(default=10, gt=0, le=120)


class PrometheusTarget(BaseModel):
    base_url: str
    promql: str
    timeout_seconds: float = Field(default=10, gt=0, le=120)


class BackupTarget(BaseModel):
    status_url: str
    timeout_seconds: float = Field(default=10, gt=0, le=120)


class DependencyTarget(BaseModel):
    kind: Literal["postgres", "redis", "minio", "tcp"]
    host: str
    port: int = Field(ge=1, le=65535)
    timeout_seconds: float = Field(default=5, gt=0, le=30)


class CollectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPS_COLLECTOR_", env_nested_delimiter="__")

    target_ref: str = "opencitadel-local"
    allowed_namespaces: list[str] = Field(default_factory=lambda: ["opencitadel"])
    allowed_workloads: dict[str, list[str]] = Field(default_factory=dict)
    prometheus_queries: dict[str, PrometheusTarget] = Field(default_factory=dict)
    http_probes: dict[str, HttpTarget] = Field(default_factory=dict)
    certificate_probes: dict[str, HttpTarget] = Field(default_factory=dict)
    backups: dict[str, BackupTarget] = Field(default_factory=dict)
    dependencies: dict[str, DependencyTarget | list[DependencyTarget]] = Field(default_factory=dict)
    transport: Literal["streamable-http", "stdio"] = "streamable-http"
    allow_stdio: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8090, ge=1, le=65535)
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
