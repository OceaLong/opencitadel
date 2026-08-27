"""Import Claude/Cursor SKILL.md into OpenCitadel Skill model."""

from __future__ import annotations

import re

from app.domain.models.skill import Skill
from app.domain.utils.slug import slugify

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter_block(block: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def parse_skill_md(content: str, *, slug: str = "") -> Skill:
    """Parse SKILL.md with optional YAML frontmatter."""
    name = ""
    description = ""
    body = content.strip()
    match = _FRONTMATTER_RE.match(content.strip())
    if match:
        meta = _parse_frontmatter_block(match.group(1))
        name = meta.get("name", "")
        description = meta.get("description", "")
        body = match.group(2).strip()
    return Skill(
        name=name or slug or "Imported Skill",
        slug=slug,
        description=description,
        system_prompt=name or description or "Imported skill instructions",
        body=body,
        source_format="claude_md",
    )


def import_skill_md(content: str, *, slug: str | None = None) -> Skill:
    skill = parse_skill_md(content, slug=slug or "")
    if not skill.slug:
        skill.slug = slugify(skill.name, fallback="skill")
    return skill
