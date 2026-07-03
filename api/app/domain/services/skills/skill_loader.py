#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Progressive skill prompt rendering."""
from __future__ import annotations

from typing import List

from app.domain.models.skill import Skill, SkillResource


def render_metadata(skill: Skill) -> str:
    """Lightweight metadata for classification and UI hints."""
    lines = [
        f"Name: {skill.name}",
        f"Description: {skill.description}",
    ]
    if skill.examples:
        lines.append("Examples:")
        for example in skill.examples[:5]:
            lines.append(f"- {example}")
    return "\n".join(lines)


def _render_resource_index(resources: List[SkillResource]) -> str:
    if not resources:
        return ""
    lines = ["Available skill resources (read via file tools when needed):"]
    for item in resources:
        loc = item.path or f"(inline:{item.name})"
        lines.append(f"- [{item.kind}] {item.name}: {loc}")
    return "\n".join(lines)


def render_active(skill: Skill) -> str:
    """Full skill instructions after a skill is selected."""
    parts: List[str] = []
    if skill.system_prompt.strip():
        parts.append(skill.system_prompt.strip())
    if skill.body.strip():
        parts.append(skill.body.strip())
    resource_index = _render_resource_index(skill.resources)
    if resource_index:
        parts.append(resource_index)
    return "\n\n".join(parts)
