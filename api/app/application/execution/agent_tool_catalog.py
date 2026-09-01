"""Production tool catalog rebuilt per Activity from immutable Run input."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic_core import to_jsonable_python

from app.application.execution.tool_catalog import ToolDefinition
from app.application.services.artifact_service import ArtifactService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.integration_server_service import (
    A2AIntegrationService,
    MCPServerService,
)
from app.application.services.memory_service import MemoryService
from app.domain.execution.activity import ActivityContext
from app.domain.execution.commands import JsonValue
from app.domain.external.connection_pool import (
    A2AConnectionPoolPort,
    MCPConnectionPoolPort,
)
from app.domain.external.file_storage import FileStorage
from app.domain.external.image_generation import ImageGenerator
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox, SandboxFactoryPort
from app.domain.external.search import SearchEngine
from app.domain.models.resource_bindings import ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.models.session_mode import SessionMode
from app.domain.models.skill import Skill
from app.domain.models.tool_policy import ApprovalMode, ToolEffect
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.artifact import ArtifactTool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.image_generation import ImageGenerationTool
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.memory import MemoryTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.tool_registry import ToolRegistry
from app.domain.vector_port import EmbeddingPort

logger = logging.getLogger(__name__)


class AgentToolCatalog:
    """Single fail-closed source for model exposure and tool execution."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        sandbox_factory: SandboxFactoryPort,
        search_engine: SearchEngine,
        mcp_connection_pool: MCPConnectionPoolPort,
        a2a_connection_pool: A2AConnectionPoolPort,
        mcp_servers: MCPServerService,
        a2a_servers: A2AIntegrationService,
        file_storage: FileStorage,
        models: InferenceModelService,
        image_generator: ImageGenerator,
        artifacts: ArtifactService,
        memories: MemoryService,
        embeddings: EmbeddingPort | None = None,
        llm_factory: Callable[..., LLM] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_factory = sandbox_factory
        self._search_engine = search_engine
        self._mcp_pool = mcp_connection_pool
        self._a2a_pool = a2a_connection_pool
        self._mcp_servers = mcp_servers
        self._a2a_servers = a2a_servers
        self._file_storage = file_storage
        self._models = models
        self._image_generator = image_generator
        self._artifacts = artifacts
        self._memories = memories
        self._embeddings = embeddings
        self._llm_factory = llm_factory

    async def definitions(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
    ) -> tuple[ToolDefinition, ...]:
        packs = await self._build(payload, context)
        try:
            return tuple(
                ToolDefinition(
                    name=descriptor.name,
                    tool_schema=descriptor.schema,
                    requires_approval=(
                        descriptor.policy.approval != ApprovalMode.NEVER
                        or descriptor.policy.effect != ToolEffect.READ_ONLY
                    ),
                    risk_summary=(f"{descriptor.policy.effect.value}: {descriptor.name}"),
                )
                for pack in packs
                for descriptor in pack.get_tool_descriptors()
            )
        finally:
            await _cleanup(packs)

    async def invoke(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        packs = await self._build(payload, context)
        try:
            matches = [
                pack
                for pack in packs
                if any(descriptor.name == name for descriptor in pack.get_tool_descriptors())
            ]
            if len(matches) != 1:
                raise ValueError("tool is absent or ambiguously registered")
            result = await matches[0].invoke(name, **arguments)
            encoded = to_jsonable_python(result)
            if not isinstance(encoded, dict):
                raise TypeError("tool result must serialize to an object")
            return encoded
        finally:
            await _cleanup(packs)

    async def retrieve(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        query: str,
    ) -> dict[str, JsonValue]:
        packs = await self._build(payload, context)
        try:
            sources: list[JsonValue] = []
            for pack in packs:
                if pack.has_tool("kb_search"):
                    result = await pack.invoke("kb_search", query=query)
                    sources.append(
                        {
                            "kind": "knowledge_base",
                            "result": to_jsonable_python(result),
                        }
                    )
            return {"query": query, "sources": sources}
        finally:
            await _cleanup(packs)

    async def _build(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
    ) -> list[BaseTool]:
        mode = SessionMode(str(payload.get("mode") or SessionMode.AGENT.value))
        family_policy = context.run.policy_snapshot.family_policy
        if family_policy.kind not in {"agent", "ask"}:
            raise ValueError("conversational tools require an agent or ask policy snapshot")
        scope = _owner_scope(context)
        skill = await self._resolve_skill(payload, scope=scope)
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id is required for conversational tools")
        bindings = _bindings(payload)
        attachments = _attachments(payload)

        async def on_sandbox_ready(sandbox: Sandbox) -> None:
            if mode != SessionMode.AGENT:
                return
            for attachment in attachments:
                file_id = attachment["file_id"]
                async with self._uow_factory() as uow:
                    file = await uow.file.get_by_id(file_id, scope=scope)
                if file is None:
                    raise ValueError("attachment is no longer accessible")
                stream, _ = await self._file_storage.download_file(file_id)
                try:
                    path = attachment["sandbox_path"]
                    directory, filename = path.rsplit("/", 1)
                    result = await sandbox.upload_file(
                        stream,
                        directory,
                        filename=filename,
                    )
                    if not result.success:
                        raise RuntimeError(result.message or "attachment upload failed")
                finally:
                    close = getattr(stream, "close", None)
                    if close is not None:
                        close()

        sandbox = _SessionSandbox(
            session_id=session_id,
            scope=scope,
            sandbox_factory=self._sandbox_factory,
            uow_factory=self._uow_factory,
            on_ready=on_sandbox_ready,
        )
        operator_scope = payload.get("operator_scope")
        raw_domains = payload.get("operator_domains", [])
        if operator_scope is not None and not isinstance(operator_scope, str):
            raise ValueError("operator_scope must be a string")
        if not isinstance(raw_domains, list) or any(
            not isinstance(domain, str) for domain in raw_domains
        ):
            raise ValueError("operator_domains must be a list of host names")
        browser = _SessionBrowser(
            sandbox,
            allowed_domains=(frozenset(raw_domains) if operator_scope is not None else None),
        )
        mcp = MCPTool(self._mcp_pool)
        a2a = A2ATool(self._a2a_pool)
        await mcp.initialize(
            await self._mcp_servers.resolve_mcp_runtime(
                scope,
                server_refs=(tuple(skill.mcp_server_refs) if skill else None),
            ),
            policy=context.run.policy_snapshot.common.activity,
        )
        await a2a.initialize(
            await self._a2a_servers.resolve_a2a_runtime(
                scope,
                server_refs=(tuple(skill.a2a_server_refs) if skill else None),
            ),
            policy=context.run.policy_snapshot.common.activity,
        )
        candidates: list[BaseTool] = [mcp, a2a]
        if mode == SessionMode.AGENT:
            model_id = payload.get("model_id")
            if model_id is not None and not isinstance(model_id, str):
                raise ValueError("model_id must be a string")
            model = await self._models.resolve_chat(model_id, scope=scope)
            candidates.extend(
                [
                    FileTool(sandbox=sandbox),
                    ShellTool(sandbox=sandbox),
                    BrowserTool(browser=browser),
                    SearchTool(search_engine=self._search_engine),
                    ImageGenerationTool(
                        inference_model=model,
                        image_generator=self._image_generator,
                        file_storage=self._file_storage,
                        owner_user_id=context.owner_user_id,
                        team_id=context.team_id,
                    ),
                ]
            )
            candidates.extend(
                self._stateful_tools(
                    session_id=session_id,
                    sandbox=sandbox,
                    context=context,
                )
            )
        rerank_llm: LLM | None = None
        if (
            self._llm_factory is not None
            and family_policy.knowledge_retrieval.rerank.enabled
            and any(b["resource_kind"] == ResourceKind.KNOWLEDGE_BASE.value for b in bindings)
        ):
            rerank_model_id = payload.get("model_id")
            if rerank_model_id is None or isinstance(rerank_model_id, str):
                try:
                    rerank_model = await self._models.resolve_chat(rerank_model_id, scope=scope)
                    rerank_llm = self._llm_factory(
                        rerank_model,
                        policy=context.run.policy_snapshot.common.model_resilience,
                        thinking_enabled=False,
                        inference_model_service=self._models,
                        scope=scope,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "kb_rerank llm unavailable, retrieval will skip rerank: %s",
                        exc,
                    )
        for binding in bindings:
            kind = binding["resource_kind"]
            if kind == ResourceKind.KNOWLEDGE_BASE.value:
                candidates.append(
                    KnowledgeBaseTool(
                        uow_factory=self._uow_factory,
                        kb_id=str(binding["resource_id"]),
                        version_id=str(binding["version_id"]),
                        policy=family_policy.knowledge_retrieval,
                        llm=rerank_llm,
                        embeddings=self._embeddings,
                        owner_scope=scope,
                    )
                )
        return ToolRegistry.build_tools(
            policy=CapabilityPolicy.for_mode(
                mode,
                allowed_tool_names=(skill.allowed_tools if skill else None),
            ),
            candidate_tools=candidates,
        )

    async def _resolve_skill(
        self,
        payload: dict[str, JsonValue],
        *,
        scope: OwnerScope,
    ) -> Skill | None:
        skill_id = payload.get("skill_id")
        if skill_id is None:
            return None
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError("skill_id must be a non-empty string")
        async with self._uow_factory() as uow:
            skill = await uow.skill.get_by_id(skill_id, scope=scope)
        if skill is None:
            raise ValueError("skill does not exist in the Run owner scope")
        if not skill.enabled:
            raise ValueError("disabled skill cannot authorize a Run")
        return skill

    def _stateful_tools(
        self,
        *,
        session_id: str,
        sandbox: Sandbox,
        context: ActivityContext,
    ) -> list[BaseTool]:
        async def save_memory(title, content, tags, scope):
            entry = await self._memories.save_from_tool(
                title=title,
                content=content,
                tags=tags,
                scope=scope,
                session_id=session_id,
                policy=context.run.policy_snapshot.family_policy.memory,
            )
            return {"id": entry.id}

        async def write_artifact(**kwargs):
            content = kwargs.get("content") or ""
            source_path = kwargs.get("source_path")
            if source_path:
                result = await sandbox.read_file(source_path, max_length=None)
                if not result.success or not isinstance(result.data, dict):
                    raise ValueError("artifact source file is unavailable")
                content = result.data.get("content")
            if not isinstance(content, str) or not content:
                raise ValueError("artifact content is required")
            artifact = await self._artifacts.write_content(
                session_id=session_id,
                artifact_id=kwargs.get("artifact_id"),
                kind=kwargs["kind"],
                title=kwargs["title"],
                content=content,
            )
            return artifact.model_dump(mode="json")

        async def finalize_artifact(artifact_id: str):
            artifact = await self._artifacts.finalize(session_id, artifact_id)
            return artifact.model_dump(mode="json")

        return [
            MemoryTool(save_fn=save_memory, session_id=session_id),
            ArtifactTool(
                write_fn=write_artifact,
                finalize_fn=finalize_artifact,
            ),
        ]


def _owner_scope(context: ActivityContext) -> OwnerScope:
    if context.owner_user_id is not None:
        return OwnerScope.personal(context.owner_user_id)
    return OwnerScope.team("execution-kernel", context.team_id or "")


def _bindings(payload: dict[str, JsonValue]) -> list[dict[str, str]]:
    raw = payload.get("resource_bindings", [])
    if not isinstance(raw, list):
        raise TypeError("resource_bindings must be a list")
    bindings: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("resource binding must be an object")
        required = ("resource_kind", "resource_id", "version_id")
        if any(not isinstance(item.get(key), str) for key in required):
            raise ValueError("resource binding is incomplete")
        bindings.append({key: str(item[key]) for key in required})
    return bindings


def _attachments(payload: dict[str, JsonValue]) -> list[dict[str, str]]:
    raw = payload.get("attachments", [])
    if not isinstance(raw, list):
        raise TypeError("attachments must be a list")
    if len(raw) > 10:
        raise ValueError("attachments must be a bounded list")
    attachments: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("attachment must be an object")
        file_id = item.get("file_id")
        filename = item.get("filename")
        sandbox_path = item.get("sandbox_path")
        if not all(isinstance(value, str) and value for value in (file_id, filename, sandbox_path)):
            raise ValueError("attachment metadata is incomplete")
        assert isinstance(file_id, str)
        assert isinstance(sandbox_path, str)
        if not sandbox_path.startswith(f"/home/ubuntu/uploads/{file_id}-"):
            raise ValueError("attachment sandbox path is invalid")
        if "/" in sandbox_path.removeprefix("/home/ubuntu/uploads/"):
            raise ValueError("attachment sandbox path is invalid")
        attachments.append(
            {
                "file_id": file_id,
                "filename": str(filename),
                "sandbox_path": sandbox_path,
            }
        )
    return attachments


async def _cleanup(packs: list[BaseTool]) -> None:
    for pack in packs:
        cleanup = getattr(pack, "cleanup", None)
        if cleanup is not None:
            await cleanup()


class _SessionSandbox:
    def __init__(
        self,
        *,
        session_id: str,
        scope: OwnerScope,
        sandbox_factory: SandboxFactoryPort,
        uow_factory: Callable[[], IUnitOfWork],
        on_ready,
    ) -> None:
        self._session_id = session_id
        self._scope = scope
        self._sandbox_factory = sandbox_factory
        self._uow_factory = uow_factory
        self._on_ready = on_ready
        self._sandbox: Sandbox | None = None

    async def _resolve(self) -> Sandbox:
        if self._sandbox is not None:
            return self._sandbox
        async with self._uow_factory() as uow:
            session = await uow.session.lock_by_id(
                self._session_id,
                scope=self._scope,
            )
            if session is None:
                raise ValueError("session does not exist")
            sandbox = (
                await self._sandbox_factory.get(session.sandbox_id) if session.sandbox_id else None
            )
            if sandbox is None:
                sandbox = await self._sandbox_factory.create(owner_scope=self._scope)
                session.sandbox_id = sandbox.id
                await uow.session.save(session)
                await uow.commit()
        await self._on_ready(sandbox)
        self._sandbox = sandbox
        return sandbox

    def __getattr__(self, name: str):
        async def call(*args, **kwargs):
            sandbox = await self._resolve()
            return await getattr(sandbox, name)(*args, **kwargs)

        return call


class _SessionBrowser:
    vision_enabled = False

    def __init__(
        self,
        sandbox: _SessionSandbox,
        *,
        allowed_domains: frozenset[str] | None,
    ) -> None:
        self._sandbox = sandbox
        self._allowed_domains = allowed_domains
        self._browser = None

    async def _resolve(self):
        if self._browser is None:
            sandbox = await self._sandbox._resolve()
            self._browser = await sandbox.get_browser(
                allowed_domains=self._allowed_domains,
            )
        return self._browser

    def __getattr__(self, name: str):
        async def call(*args, **kwargs):
            browser = await self._resolve()
            return await getattr(browser, name)(*args, **kwargs)

        return call


__all__ = ["AgentToolCatalog"]
