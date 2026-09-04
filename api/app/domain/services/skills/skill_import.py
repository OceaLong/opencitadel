"""Import Claude/Cursor SKILL.md into OpenCitadel Skill model."""

from __future__ import annotations

import re

import yaml

from app.domain.models.skill import Skill
from app.domain.utils.slug import slugify

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter_block(block: str) -> dict[str, object]:
    """真 YAML 解析（D11）：支持列表等标量以外的声明，坏 YAML 视为无 frontmatter。"""
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_allowed_tools(raw: object) -> list[str] | None:
    """显式白名单语义：无声明 → None（不限制）；声明列表 → 逐项字符串化。

    也接受 Claude 风格的逗号分隔字符串（``allowed-tools: a, b``）。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",")]
        return [item for item in items if item]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return None


def parse_skill_md(content: str, *, slug: str = "") -> Skill:
    """Parse SKILL.md with optional YAML frontmatter."""
    name = ""
    description = ""
    allowed_tools: list[str] | None = None
    body = content.strip()
    match = _FRONTMATTER_RE.match(content.strip())
    if match:
        meta = _parse_frontmatter_block(match.group(1))
        name = str(meta.get("name") or "")
        description = str(meta.get("description") or "")
        raw_tools = meta.get("allowed_tools", meta.get("allowed-tools"))
        allowed_tools = _parse_allowed_tools(raw_tools)
        body = match.group(2).strip()
    return Skill(
        name=name or slug or "Imported Skill",
        slug=slug,
        description=description,
        system_prompt=name or description or "Imported skill instructions",
        body=body,
        allowed_tools=allowed_tools,
        source_format="claude_md",
    )


def import_skill_md(content: str, *, slug: str | None = None) -> Skill:
    skill = parse_skill_md(content, slug=slug or "")
    if not skill.slug:
        skill.slug = slugify(skill.name, fallback="skill")
    return skill
