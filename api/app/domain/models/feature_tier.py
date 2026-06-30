#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feature capability tiers for model isolation governance."""
from enum import Enum
from typing import Literal

ModelDependency = Literal["none", "optional", "required"]


class FeatureTier(str, Enum):
    """L0–L3 capability tiers mapped to failure domains."""

    L0_PLATFORM = "L0"  # Platform infra only (Postgres/Redis/COS)
    L1_DATA = "L1"  # No model dependency
    L1_CONFIG = "L1'"  # Config surface; may touch model on explicit probe
    L2_ENHANCED = "L2"  # Model-enhanced but degradable
    L3_MODEL_REQUIRED = "L3"  # Hard model dependency


def tier_to_model_dependency(tier: FeatureTier) -> ModelDependency:
    if tier in (FeatureTier.L0_PLATFORM, FeatureTier.L1_DATA, FeatureTier.L1_CONFIG):
        return "none"
    if tier == FeatureTier.L2_ENHANCED:
        return "optional"
    return "required"


# Route / service capability matrix (single source for documentation & gates).
FEATURE_CAPABILITY_MATRIX: dict[str, FeatureTier] = {
    # L0
    "status.check": FeatureTier.L0_PLATFORM,
    "metrics.read": FeatureTier.L0_PLATFORM,
    "app_config.crud": FeatureTier.L0_PLATFORM,
    # L1
    "file.upload": FeatureTier.L1_DATA,
    "file.download": FeatureTier.L1_DATA,
    "room.crud": FeatureTier.L1_DATA,
    "questionnaire.crud": FeatureTier.L1_DATA,
    "session.metadata": FeatureTier.L1_DATA,
    "skill.crud": FeatureTier.L1_DATA,
    "memory.crud": FeatureTier.L1_DATA,
    "marketplace.list_apps": FeatureTier.L1_DATA,
    "marketplace.consumption_manual": FeatureTier.L1_DATA,
    "marketplace.consumption_correct": FeatureTier.L1_DATA,
    "marketplace.convert_document": FeatureTier.L1_DATA,
    # L1' — config writes no longer sync-probe models
    "llm_model.crud": FeatureTier.L1_CONFIG,
    "llm_model.probe": FeatureTier.L3_MODEL_REQUIRED,
    # L2
    "marketplace.route": FeatureTier.L2_ENHANCED,
    "marketplace.video_search": FeatureTier.L2_ENHANCED,
    "marketplace.fortune": FeatureTier.L2_ENHANCED,
    "memory.recall_vector": FeatureTier.L2_ENHANCED,
    "memory.extract": FeatureTier.L2_ENHANCED,
    # L3
    "agent.chat": FeatureTier.L3_MODEL_REQUIRED,
    "agent.code_ask": FeatureTier.L3_MODEL_REQUIRED,
    "marketplace.nutrition": FeatureTier.L3_MODEL_REQUIRED,
    "marketplace.document_qa": FeatureTier.L3_MODEL_REQUIRED,
    "marketplace.translation": FeatureTier.L3_MODEL_REQUIRED,
    "marketplace.watermark_ai": FeatureTier.L3_MODEL_REQUIRED,
    "codebase.vector_index": FeatureTier.L3_MODEL_REQUIRED,
    "a2a.inbound": FeatureTier.L3_MODEL_REQUIRED,
    "audio.transcription": FeatureTier.L3_MODEL_REQUIRED,
    "image.generation": FeatureTier.L3_MODEL_REQUIRED,
}
