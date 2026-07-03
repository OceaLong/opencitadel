#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Merge global AppConfig with optional per-user overrides."""
from typing import Optional

from app.application.errors.exceptions import BadRequestError
from app.domain.models.app_config import AppConfig
from app.domain.models.app_config_scope import (
    GLOBAL_ONLY_SECTIONS,
    USER_OVERRIDABLE_SECTIONS,
)
from app.domain.models.scope import OwnerScope


def validate_user_override_payload(payload: dict) -> None:
    invalid = set(payload.keys()) - USER_OVERRIDABLE_SECTIONS
    if invalid:
        raise BadRequestError(f"用户覆盖不允许修改以下配置段: {', '.join(sorted(invalid))}")


def validate_global_section(section: str) -> None:
    if section not in GLOBAL_ONLY_SECTIONS and section not in USER_OVERRIDABLE_SECTIONS:
        raise BadRequestError(f"未知配置段: {section}")


def merge_configs(global_config: AppConfig, override_payload: Optional[dict]) -> AppConfig:
    if not override_payload:
        return global_config
    merged = global_config.model_copy(deep=True)
    for section in USER_OVERRIDABLE_SECTIONS:
        if section in override_payload and override_payload[section] is not None:
            section_type = type(getattr(global_config, section))
            setattr(merged, section, section_type.model_validate(override_payload[section]))
    return merged


async def resolve_config_for_owner(
    repository,
    scope: Optional[OwnerScope],
) -> AppConfig:
    global_config = await repository.load_global() or AppConfig()
    if scope is None:
        return global_config
    override_payload = await repository.load_user_override_payload(scope.user_id)
    return merge_configs(global_config, override_payload)
