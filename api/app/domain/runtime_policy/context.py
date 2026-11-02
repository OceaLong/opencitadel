"""Frozen context settings, independent of definition and payload schema versions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format_version: Literal[1] = 1
    max_input_tokens: int = Field(default=32768, ge=1024, le=1048576)
    main_output_tokens: int = Field(default=4096, ge=256, le=32768)
    summary_output_tokens: int = Field(default=1024, ge=256, le=4096)
    selected_sources: int = Field(default=128, ge=4, le=256)
    recent_units: int = Field(default=4, ge=1, le=32)
    read_bytes: int = Field(default=65536, ge=1024, le=262144)
    prepare_bytes: int = Field(default=8388608, ge=65536, le=8388608)
    compactions_per_round: int = Field(default=2, ge=0, le=4)
    compactions_per_run: int = Field(default=128, ge=0, le=128)
    ask_retrievals: int = Field(default=2, ge=0, le=4)
