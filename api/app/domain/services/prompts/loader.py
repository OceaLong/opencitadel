#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Locale-aware prompt loading for agents."""
from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Optional

from app.domain.services.prompts.locale import resolve_prompt_locale


@dataclass(frozen=True)
class PromptBundle:
    system: ModuleType
    planner: ModuleType
    react: ModuleType
    clarify: ModuleType


def load_prompts(
        working_language: Optional[str] = None,
        config_locale: Optional[str] = None,
) -> PromptBundle:
    locale = resolve_prompt_locale(working_language, config_locale)
    if locale == "zh":
        from app.domain.services.prompts import zh as locale_pkg

        return PromptBundle(
            system=locale_pkg.system,
            planner=locale_pkg.planner,
            react=locale_pkg.react,
            clarify=locale_pkg.clarify,
        )

    from app.domain.services.prompts import en as locale_pkg

    return PromptBundle(
        system=locale_pkg.system,
        planner=locale_pkg.planner,
        react=locale_pkg.react,
        clarify=locale_pkg.clarify,
    )


def detect_locale_from_text(text: Optional[str]) -> str:
    if not text:
        return resolve_prompt_locale()
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return "zh"
    return resolve_prompt_locale(working_language=text)


def compose_system_prompt(prompts: PromptBundle, extra: str = "") -> str:
    return prompts.system.SYSTEM_PROMPT + extra
