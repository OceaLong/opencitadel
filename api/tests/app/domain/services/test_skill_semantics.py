"""Skill allowed_tools semantics (D11): None=不限制, []=禁全部; YAML frontmatter."""

from __future__ import annotations

import pytest

from app.domain.models.session_mode import SessionMode
from app.domain.models.skill import Skill
from app.domain.services.skills.skill_import import import_skill_md, parse_skill_md
from app.domain.services.tools.capability_policy import READ_SAFE, CapabilityPolicy


@pytest.mark.parametrize(
    ("allowed_tools", "tool_name", "expected"),
    [
        (None, "read_file", True),
        (None, "mcp_jina_read_url", True),
        ([], "read_file", False),
        ([], "kb_search", False),
        (["read_file"], "read_file", True),
        (["read_file"], "write_file", False),
    ],
)
def test_capability_policy_allowlist_semantics(allowed_tools, tool_name, expected):
    policy = CapabilityPolicy.for_mode(
        SessionMode.AGENT,
        allowed_tool_names=allowed_tools,
    )

    assert policy.allows(READ_SAFE, tool_name=tool_name) is expected


def test_skill_model_defaults_to_unrestricted_tools():
    assert Skill(name="s", slug="s").allowed_tools is None


def test_frontmatter_without_declaration_imports_as_unrestricted():
    skill = import_skill_md("---\nname: Helper\n---\nBody text")

    assert skill.allowed_tools is None
    assert skill.name == "Helper"


def test_frontmatter_yaml_list_is_parsed():
    content = (
        "---\n"
        "name: Coder\n"
        "description: writes code\n"
        "allowed_tools:\n"
        "  - read_file\n"
        "  - write_file\n"
        "---\n"
        "Body"
    )
    skill = parse_skill_md(content, slug="coder")

    assert skill.allowed_tools == ["read_file", "write_file"]
    assert skill.description == "writes code"


def test_frontmatter_claude_style_comma_string_is_parsed():
    content = "---\nname: Ops\nallowed-tools: read_file, shell_execute\n---\nBody"
    skill = parse_skill_md(content)

    assert skill.allowed_tools == ["read_file", "shell_execute"]


def test_frontmatter_empty_list_means_deny_all():
    content = "---\nname: Locked\nallowed_tools: []\n---\nBody"
    skill = parse_skill_md(content)

    assert skill.allowed_tools == []


def test_broken_yaml_frontmatter_degrades_to_unrestricted_body_import():
    content = "---\nname: [unclosed\n---\nBody"
    skill = parse_skill_md(content, slug="broken")

    assert skill.allowed_tools is None
    assert skill.body == "Body"
