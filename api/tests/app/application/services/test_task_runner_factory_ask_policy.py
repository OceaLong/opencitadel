from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.app_config import AgentConfig
from app.domain.models.codebase import Codebase
from app.domain.models.codebase import SessionMode
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import (
    ResourceBindingProjection,
    ResourceKind,
)
from app.domain.models.session import Session
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.domain.services.tools.tool_registry import ToolRegistry
from tests.app.application.services.test_task_runner_factory import (
    _FakeUow,
    _build_factory,
    _llm_model,
    _runtime_config,
)


class _UnconfiguredIntegrationTool(BaseTool):
    name = "integration"

    @tool(name="create_ticket", description="write", parameters={}, required=[])
    async def create_ticket(self):
        return {"ok": True}


def test_ask_flow_registry_hides_unconfigured_extra_integration():
    integration = _UnconfiguredIntegrationTool()

    tools = ToolRegistry.build_ask_tools(
        mcp_tool=MagicMock(),
        a2a_tool=MagicMock(),
        extra_tools=[integration],
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
    )

    schemas = ToolRegistry.collect_schemas(tools)
    names = {schema["function"]["name"] for schema in schemas}
    assert "create_ticket" not in names


@pytest.mark.asyncio
async def test_task_runner_factory_builds_real_ask_flow_without_write_or_delegate_tools():
    codebase = Codebase(
        id="cb-1",
        name="demo",
        sandbox_id="ingestion-1",
        workspace_path="/workspace/demo",
        active_version_id="cbv1",
    )
    codebase_version = CodebaseVersion(
        id="cbv1",
        codebase_id=codebase.id,
        state=CodebaseVersionState.READY,
        published_at=datetime.now(timezone.utc),
        source_snapshot_key="snapshots/cbv1.tgz",
        source_digest="digest-cbv1",
    )
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=MagicMock())
    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase, codebase_version)
    factory._object_storage = MagicMock()
    factory._memory_service.save_from_tool = AsyncMock()
    llm = MagicMock()
    llm.supports_multimodal = False
    session = Session(
        id="session-1",
        codebase_id=codebase.id,
        mode=SessionMode.ASK,
        model_id="model-1",
        owner_user_id="user-1",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-cbv1",
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase.id,
                version_id="cbv1",
            )
        ],
    )

    with patch.object(
        factory,
        "_resolve_llm_and_config",
        AsyncMock(return_value=(llm, AgentConfig(), None, "", "", _llm_model())),
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        runner = await factory.create_runner(session)

    names = {
        schema["function"]["name"]
        for tool_pack in runner._flow._agent._tools
        for schema in tool_pack.get_tools()
    }
    assert {"semantic_search", "read_code"} <= names
    assert not {"memory_save", "delegate_subtask", "write_file", "shell_execute"} & names
