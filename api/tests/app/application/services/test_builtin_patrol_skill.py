from app.application.services.skill_service import BUILTIN_SKILLS


def test_builtin_patrol_skill_has_fixed_read_only_contract():
    skill = next(item for item in BUILTIN_SKILLS if item.slug == "ops-patrol")
    assert skill.auto_recommend is False
    assert skill.agent_params.max_iterations == 40
    assert skill.agent_params.max_retries == 2
    assert skill.agent_params.temperature_override == 0.0
    assert len([name for name in skill.allowed_tools if name.startswith("mcp_")]) == 9
    forbidden = ("shell_", "browser_", "a2a", "write_file")
    assert not any(name.startswith(forbidden) for name in skill.allowed_tools)
    assert "不可信数据" in skill.system_prompt
    assert "最终状态由服务端断言引擎计算" in skill.system_prompt
