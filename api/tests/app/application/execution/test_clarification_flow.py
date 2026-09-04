"""ask_user 澄清选项卡片的全链路契约（声明式、无工具名特判）。

链路：模型调用 ask_user(question, options) → 规范化把 question/options 提升为
审批卡片元数据 → planner 走标准审批等待（RequestApproval 带 choices）→ 用户
点选即 approved+feedback → planner 把 feedback 放进 tool.call input_payload →
catalog 按工具声明的 feedback 参数注入 → 工具把选择作为结果回流模型。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.execution.activities.model_call import _normalize_tool_calls
from app.application.execution.decisions import next_command
from app.application.execution.decisions.base import (
    activity_identity,
    approval_identity,
)
from app.application.execution.run_context import run_execution_context
from app.application.execution.tool_catalog import ToolDefinition
from app.domain.execution.run import RunFamily, RunState, RunStatus, decision_data_digest
from app.domain.services.tools.ask_user import AskUserTool
from app.domain.services.tools.capability_policy import CLARIFICATION_INTERACTIVE
from tests.app.execution_test_support import run_policy_snapshot_json

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
RUN_ID = UUID("70000000-0000-0000-0000-000000000001")

_ASK_USER_DEFINITION = ToolDefinition(
    name="ask_user",
    tool_schema={"type": "function", "function": {"name": "ask_user"}},
    requires_approval=True,
    risk_summary="interactive: ask_user",
    approval_kind="clarification",
    approval_prompt_param="question",
    approval_choices_param="options",
)


def test_clarification_policy_is_declarative_and_gates_execution() -> None:
    policy = CLARIFICATION_INTERACTIVE
    assert policy.requires_approval() is True
    assert policy.approval_kind == "clarification"
    assert policy.approval_prompt_param == "question"
    assert policy.approval_choices_param == "options"
    assert policy.approval_feedback_param == "resolved_choice"


def test_normalization_promotes_question_and_options_to_card_metadata() -> None:
    normalized = _normalize_tool_calls(
        [
            {
                "id": "call-1",
                "function": {
                    "name": "ask_user",
                    "arguments": {
                        "question": "部署到哪个环境？",
                        "options": ["预发环境", "生产环境", "  ", 42],
                    },
                },
            }
        ],
        (_ASK_USER_DEFINITION,),
    )

    assert normalized is not None
    (call,) = normalized
    assert call["approval_kind"] == "clarification"
    # 卡片提示文本来自工具声明的参数，而非派生风险摘要。
    assert call["risk_summary"] == "部署到哪个环境？"
    # 非法项被丢弃，仅保留干净的字符串选项。
    assert call["approval_choices"] == ["预发环境", "生产环境"]


def _agent_state(**updates) -> RunState:
    base = RunState(
        run_id=RUN_ID,
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={"input_ref": "object://input", "input_digest": "a" * 64},
        policy_snapshot=run_policy_snapshot_json("agent"),
        status=RunStatus.RUNNING,
        stream_version=6,
        owner_user_id="user-1",
        correlation_id=UUID(int=9),
    )
    return base.model_copy(update=updates)


def _clarifying_model_round() -> tuple[RunState, UUID, dict, dict]:
    running = _agent_state()
    retrieval_id = activity_identity(running, "retrieval:0")
    model_id = activity_identity(running, "model:0")
    decision = {
        "tool_calls": [
            {
                "call_id": "ask-1",
                "name": "ask_user",
                "arguments": {
                    "question": "部署到哪个环境？",
                    "options": ["预发环境", "生产环境"],
                },
                "requires_approval": True,
                "risk_summary": "部署到哪个环境？",
                "approval_kind": "clarification",
                "approval_choices": ["预发环境", "生产环境"],
            }
        ]
    }
    state = running.model_copy(
        update={
            "settled_activities": (
                (retrieval_id, "succeeded", 0),
                (model_id, "succeeded", 0),
            ),
            "activity_results": (
                (retrieval_id, 0, "result://retrieval", None, None),
                (model_id, 0, "result://model-0", None, decision_data_digest(decision)),
            ),
        }
    )
    return state, model_id, decision, {"model_id": model_id}


def test_planner_requests_a_clarification_approval_with_choices() -> None:
    state, model_id, decision, _ = _clarifying_model_round()

    request = next_command(
        state,
        run_execution_context(state),
        outcomes={model_id: decision},
        now=NOW,
    )

    assert request is not None
    assert request.command_type == "RequestApproval"
    assert request.payload["approval_kind"] == "clarification"
    assert request.payload["risk_summary"] == "部署到哪个环境？"
    assert request.payload["choices"] == ["预发环境", "生产环境"]
    assert request.payload["subject_label"] == "ask_user"


def test_users_choice_rides_into_the_tool_call_as_approval_feedback() -> None:
    state, model_id, decision, _ = _clarifying_model_round()
    tool_key = "tool:0:0:ask-1"
    approval_id = approval_identity(state, tool_key)
    approved = state.model_copy(
        update={
            "approval_decisions": ((approval_id, "approved", "生产环境"),),
            "stream_version": 8,
        }
    )

    request = next_command(
        approved,
        run_execution_context(approved),
        outcomes={model_id: decision},
        now=NOW,
    )

    assert request is not None
    assert request.command_type == "RequestActivity"
    assert request.payload["input_payload"]["approval_feedback"] == "生产环境"
    assert request.payload["input_payload"]["tool_call"]["name"] == "ask_user"


@pytest.mark.asyncio
async def test_ask_user_tool_returns_the_users_choice_to_the_model() -> None:
    tool = AskUserTool()

    chosen = await tool.invoke(
        "ask_user",
        question="部署到哪个环境？",
        options=["预发环境", "生产环境"],
        resolved_choice="生产环境",
    )
    unchosen = await tool.invoke(
        "ask_user",
        question="部署到哪个环境？",
        options=["预发环境", "生产环境"],
    )

    assert chosen.success is True
    assert chosen.data["choice"] == "生产环境"
    assert "生产环境" in (chosen.message or "")
    assert unchosen.success is True
    assert unchosen.data["choice"] is None


def test_ask_user_is_assembled_for_agent_mode_only() -> None:
    from app.application.execution.agent_tool_catalog import _TOOL_ASSEMBLY
    from app.domain.services.tools.tool_specs import AGENT_ONLY

    spec = next(item.spec for item in _TOOL_ASSEMBLY if item.spec.name == "ask_user")
    assert spec.modes == AGENT_ONLY


def test_clarification_public_events_are_ask_not_approval() -> None:
    """澄清不是审批：公共事件独立为 ask 三态，审批事件保持纯审批形态。"""
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.domain.execution.events import StoredEvent
    from app.infrastructure.execution.postgres_formal_projector import (
        PostgresFormalProjector,
    )

    def stored(event_type: str, payload: dict) -> StoredEvent:
        return StoredEvent(
            position=1,
            event_id=uuid4(),
            stream_type="run",
            stream_id=str(RUN_ID),
            stream_version=1,
            event_type=event_type,
            event_schema_version=1,
            public_payload=payload,
            internal_payload={},
            secret_ref=None,
            owner_user_id="user-1",
            team_id=None,
            correlation_id=uuid4(),
            causation_id=None,
            occurred_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
            prev_hash="0" * 64,
            event_hash="1" * 64,
        )

    approval_id = str(UUID(int=81))
    requested = PostgresFormalProjector._public_shape(
        stored(
            "ApprovalRequested",
            {
                "approval_id": approval_id,
                "subject_activity_id": str(UUID(int=82)),
                "approval_kind": "clarification",
                "risk_summary": "部署到哪个环境？",
                "subject_label": "ask_user",
                "choices": ["预发环境", "生产环境"],
            },
        )
    )
    assert requested is not None
    kind, payload = requested
    assert kind == "ask"
    assert payload["status"] == "pending"
    assert payload["question"] == "部署到哪个环境？"
    assert payload["choices"] == ["预发环境", "生产环境"]

    resolved = PostgresFormalProjector._public_shape(
        stored(
            "ApprovalDecided",
            {
                "approval_id": approval_id,
                "decision": "approved",
                "actor_user_id": "user-1",
                "feedback": "生产环境",
            },
        ),
        approval_kind="clarification",
    )
    assert resolved is not None
    assert resolved[0] == "ask"
    assert resolved[1]["status"] == "resolved"
    assert resolved[1]["choice"] == "生产环境"

    expired = PostgresFormalProjector._public_shape(
        stored("ApprovalExpired", {"approval_id": approval_id}),
        approval_kind="clarification",
    )
    assert expired is not None
    assert expired[0] == "ask"
    assert expired[1]["status"] == "expired"

    # 纯审批形态不受影响，且不再携带澄清字段。
    plain = PostgresFormalProjector._public_shape(
        stored(
            "ApprovalRequested",
            {
                "approval_id": approval_id,
                "subject_activity_id": str(UUID(int=82)),
                "approval_kind": "tool_effect",
                "risk_summary": "Write workspace file",
                "subject_label": "write_file",
            },
        )
    )
    assert plain is not None
    assert plain[0] == "approval"
    assert "choices" not in plain[1]["payload"]
