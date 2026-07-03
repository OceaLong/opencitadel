#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Locale-aware prompt loading for agents."""
from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Optional

from app.domain.models.app_config import SandboxRuntimeConfig
from app.domain.services.prompts.locale import resolve_prompt_locale


@dataclass(frozen=True)
class PromptBundle:
    locale: str
    system: ModuleType
    planner: ModuleType
    react: ModuleType
    clarify: ModuleType
    internal: ModuleType
    flows: ModuleType


def load_prompts(
        working_language: Optional[str] = None,
        config_locale: Optional[str] = None,
) -> PromptBundle:
    locale = resolve_prompt_locale(working_language, config_locale)
    if locale == "zh":
        from app.domain.services.prompts import zh as locale_pkg

        return PromptBundle(
            locale=locale,
            system=locale_pkg.system,
            planner=locale_pkg.planner,
            react=locale_pkg.react,
            clarify=locale_pkg.clarify,
            internal=locale_pkg.internal,
            flows=locale_pkg.flows,
        )

    from app.domain.services.prompts import en as locale_pkg

    return PromptBundle(
        locale=locale,
        system=locale_pkg.system,
        planner=locale_pkg.planner,
        react=locale_pkg.react,
        clarify=locale_pkg.clarify,
        internal=locale_pkg.internal,
        flows=locale_pkg.flows,
    )


def detect_locale_from_text(text: Optional[str]) -> str:
    if not text:
        return resolve_prompt_locale()
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return "zh"
    return resolve_prompt_locale(working_language=text)


def render_sandbox_environment(cfg: SandboxRuntimeConfig, locale: str) -> str:
    if locale == "zh":
        return f"""<sandbox_environment>
系统环境:
- {cfg.os}，具备互联网访问权限
- 用户: `{cfg.user}`，拥有 sudo 权限
- 主目录: {cfg.home}

开发环境:
- Python {cfg.python_version} (命令: python3, pip3)
- Node.js {cfg.node_version} (命令: node, npm)
- 基础计算器 (命令: {cfg.extra_tools})
</sandbox_environment>"""
    return f"""<sandbox_environment>
System environment:
- {cfg.os} with internet access
- User: `{cfg.user}` with sudo privileges
- Home directory: {cfg.home}

Development environment:
- Python {cfg.python_version} (commands: python3, pip3)
- Node.js {cfg.node_version} (commands: node, npm)
- Basic calculator (command: {cfg.extra_tools})
</sandbox_environment>"""


def resolve_writing_style(
        writing_style_override: Optional[str] = None,
        override_base_rules: bool = False,
        global_default: str = "prose",
) -> str:
    if override_base_rules:
        return "adaptive"
    if writing_style_override in ("prose", "adaptive"):
        return writing_style_override
    return global_default if global_default in ("prose", "adaptive") else "prose"


def compose_system_prompt(
        prompts: PromptBundle,
        extra: str = "",
        *,
        sandbox_runtime: SandboxRuntimeConfig,
        writing_style: str = "prose",
) -> str:
    system_mod = prompts.system
    if writing_style == "adaptive":
        writing_block = system_mod.WRITING_RULES_ADAPTIVE
        file_block = system_mod.FILE_RULES_ADAPTIVE
    else:
        writing_block = system_mod.WRITING_RULES_PROSE
        file_block = system_mod.FILE_RULES_PROSE

    base = system_mod.SYSTEM_PROMPT
    base = base.replace("{file_rules}", file_block)
    base = base.replace("{tool_use_discipline}", system_mod.TOOL_USE_DISCIPLINE)
    base = base.replace("{writing_rules}", writing_block)
    base = base.replace(
        "{sandbox_environment}",
        render_sandbox_environment(sandbox_runtime, prompts.locale),
    )
    return base + extra


def get_internal_prompts(locale: Optional[str] = None) -> ModuleType:
    resolved = resolve_prompt_locale(locale)
    if resolved == "zh":
        from app.domain.services.prompts import zh as locale_pkg
        return locale_pkg.internal
    from app.domain.services.prompts import en as locale_pkg
    return locale_pkg.internal
