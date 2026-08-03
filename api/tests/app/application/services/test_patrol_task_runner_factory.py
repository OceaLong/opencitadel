from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.patrol_templates import load_patrol_template
from app.application.services.skill_service import BUILTIN_SKILLS
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.domain.models.app_config import AgentConfig, AppConfig, MCPConfig, MCPServerConfig
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.llm_model import LLMModel
from app.domain.models.patrol import PatrolPack, PatrolPackStatus, PatrolRun, PatrolTriggerType
from app.domain.models.session import Session, SessionMode
from app.domain.services.tools.patrol import PatrolTool


class _PatrolRepo:
    def __init__(self, run: PatrolRun, pack: PatrolPack):
        self.run = run
        self.pack = pack

    async def get_run_by_session_id(self, session_id: str):
        return self.run if self.run.session_id == session_id else None

    async def get_run(self, run_id: str, scope=None):
        return self.run if self.run.id == run_id else None

    async def get_pack(self, pack_id: str, scope=None):
        return self.pack if self.pack.id == pack_id else None


class _Uow:
    def __init__(self, run: PatrolRun, pack: PatrolPack, server: MCPServerRecord):
        self.patrol = _PatrolRepo(run, pack)
        self.mcp_server = SimpleNamespace(get_by_id=AsyncMock(return_value=server))
        self.session = SimpleNamespace(list_events=AsyncMock(return_value=[]))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _fixture(capability_hash: str = "c" * 64):
    config = load_patrol_template("kubernetes-baseline-v1")
    config.checks = config.checks[:1]
    config.defaults.run_timeout_seconds = 321
    skill = next(item for item in BUILTIN_SKILLS if item.slug == "ops-patrol").model_copy(deep=True)
    server = MCPServerRecord(
        id="server-1",
        name="collector",
        url="https://collector.example/mcp",
    )
    pack = PatrolPack(
        id="pack-1",
        owner_user_id="user-1",
        name="Daily",
        slug="daily",
        status=PatrolPackStatus.ACTIVE,
        config=config,
        mcp_server_id=server.id,
        skill_id=skill.id,
    )
    run = PatrolRun(
        id="run-1",
        pack_id=pack.id,
        session_id="session-1",
        pack_version=1,
        pack_snapshot={
            "mcp_server_id": server.id,
            "config": config.model_dump(mode="json"),
            "enabled_tools": ["get_capabilities", "k8s_workload_summary"],
        },
        trigger_type=PatrolTriggerType.MANUAL,
        idempotency_key="trigger-1",
        collector_capability_hash=capability_hash,
    )
    return config, skill, server, pack, run


def _factory(uow: _Uow, patrol_service, server: MCPServerRecord) -> TaskRunnerFactory:
    runtime = AppConfig(
        agent_config=AgentConfig(),
        mcp_config=MCPConfig(
            mcpServers={
                server.name: MCPServerConfig(url=server.url, enabled=True),
                "unrelated": MCPServerConfig(url="https://other.example/mcp", enabled=True),
            }
        ),
    )
    config_provider = SimpleNamespace(
        resolve_for_owner=AsyncMock(return_value=runtime),
        get=AsyncMock(return_value=runtime),
    )
    return TaskRunnerFactory(
        uow_factory=lambda: uow,
        llm_model_service=MagicMock(),
        skill_service=MagicMock(),
        memory_service=MagicMock(),
        sandbox_cls=MagicMock(),
        json_parser=MagicMock(),
        search_engine=MagicMock(),
        file_storage=MagicMock(),
        config_provider=config_provider,
        checkpoint_service=MagicMock(),
        task_state_port=MagicMock(),
        observability_port=MagicMock(),
        event_sequence_port=MagicMock(),
        mcp_connection_pool=MagicMock(),
        a2a_connection_pool=MagicMock(),
        patrol_run_service=patrol_service,
    )


@pytest.mark.asyncio
async def test_patrol_runner_isolated_to_one_collector_and_submission_tool():
    config, skill, server, pack, run = _fixture()
    patrol_service = SimpleNamespace(finalize_run=AsyncMock(), mark_run_failed=AsyncMock())
    factory = _factory(_Uow(run, pack, server), patrol_service, server)
    session = Session(
        id=run.session_id,
        owner_user_id="user-1",
        skill_id=skill.id,
        model_id="model-1",
        mode=SessionMode.AGENT,
    )
    llm = MagicMock(supports_multimodal=False)
    model = LLMModel(
        id="model-1",
        name="test-model",
        provider="openai",
        model="gpt-test",
        endpoint_id="endpoint-1",
    )
    captured = {}

    def capture_runner(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with (
        patch.object(
            factory,
            "_resolve_llm_and_config",
            AsyncMock(return_value=(llm, AgentConfig(), skill, "patrol prompt", "", model)),
        ),
        patch(
            "app.application.services.task_runner_factory.MCPPatrolCollectorValidator.get_capabilities",
            AsyncMock(return_value={"overall_capability_hash": run.collector_capability_hash}),
        ),
        patch("app.application.services.task_runner_factory.build_subagent_tool", return_value=MagicMock()),
        patch("app.application.services.task_runner_factory.AgentTaskRunner", side_effect=capture_runner),
        patch("app.application.services.task_runner_factory.get_runtime_config", return_value=AppConfig()),
    ):
        await factory.create_runner(session)

    assert captured["agent_config"].max_run_seconds == config.defaults.run_timeout_seconds
    assert list(captured["mcp_config"].mcpServers) == [server.name]
    assert captured["a2a_config"].a2a_servers == []
    assert len(captured["extra_tools"]) == 1
    assert isinstance(captured["extra_tools"][0], PatrolTool)
    assert captured["on_complete_callback"] is None


@pytest.mark.asyncio
async def test_patrol_runner_rejects_live_capability_drift_before_construction():
    _config, skill, server, pack, run = _fixture()
    factory = _factory(
        _Uow(run, pack, server),
        SimpleNamespace(finalize_run=AsyncMock(), mark_run_failed=AsyncMock()),
        server,
    )
    session = Session(
        id=run.session_id,
        owner_user_id="user-1",
        skill_id=skill.id,
        model_id="model-1",
        mode=SessionMode.AGENT,
    )
    llm = MagicMock(supports_multimodal=False)
    model = LLMModel(id="model-1", name="test", provider="openai", model="gpt-test", endpoint_id="endpoint-1")

    with (
        patch.object(
            factory,
            "_resolve_llm_and_config",
            AsyncMock(return_value=(llm, AgentConfig(), skill, "", "", model)),
        ),
        patch(
            "app.application.services.task_runner_factory.MCPPatrolCollectorValidator.get_capabilities",
            AsyncMock(return_value={"overall_capability_hash": "d" * 64}),
        ),
        patch("app.application.services.task_runner_factory.get_runtime_config", return_value=AppConfig()),
    ):
        with pytest.raises(NotFoundError, match="capability drift"):
            await factory.create_runner(session)
