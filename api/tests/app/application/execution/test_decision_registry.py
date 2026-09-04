"""D10 guards: family planner registry and activity-type single source."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.application.execution import activity_types
from app.application.execution.activities import (
    ChildRunActivityHandler,
    KnowledgeBuildActivityHandler,
    ModelCallActivityHandler,
    PatrolExecutionActivityHandler,
    PatrolValidationActivityHandler,
    RemediationActivityHandler,
    RetrievalActivityHandler,
    ToolCallActivityHandler,
)
from app.application.execution.decisions import (
    DECISION_PLANNERS,
    validate_decision_registry,
)
from app.domain.execution.run import RunFamily

API_ROOT = Path(__file__).parents[4]

_ALL_HANDLER_TYPES = {
    handler.activity_type
    for handler in (
        ChildRunActivityHandler,
        KnowledgeBuildActivityHandler,
        ModelCallActivityHandler,
        PatrolExecutionActivityHandler,
        PatrolValidationActivityHandler,
        RemediationActivityHandler,
        RetrievalActivityHandler,
        ToolCallActivityHandler,
    )
}


def test_every_run_family_has_a_registered_planner() -> None:
    assert set(DECISION_PLANNERS) == set(RunFamily)


def test_planner_emits_are_subset_of_admitted_handler_types() -> None:
    # 绿例：生产 handler 集合完全覆盖决策侧声明。
    validate_decision_registry(_ALL_HANDLER_TYPES)


def test_missing_handler_type_fails_startup_validation() -> None:
    # 红例：缺一个 handler 类型必须在启动期报错，而不是运行时才发现。
    with pytest.raises(ValueError, match=re.escape("tool.call")):
        validate_decision_registry(_ALL_HANDLER_TYPES - {activity_types.TOOL_CALL})


def test_handler_types_match_single_source_constants() -> None:
    assert _ALL_HANDLER_TYPES == activity_types.ALL_ACTIVITY_TYPES


def test_activity_type_literals_only_live_in_activity_types_module() -> None:
    """Activity 类型字符串字面量只允许出现在 activity_types.py（与测试）。"""
    pattern = re.compile(
        "|".join(re.escape(f'"{literal}"') for literal in sorted(activity_types.ALL_ACTIVITY_TYPES))
    )
    offenders: list[str] = []
    for path in sorted((API_ROOT / "app").rglob("*.py")):
        if path.name == "activity_types.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(API_ROOT)))
    assert offenders == []
