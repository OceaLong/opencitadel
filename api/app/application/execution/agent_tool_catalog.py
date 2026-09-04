"""Production tool catalog rebuilt per Activity from immutable Run input.

The assembly is spec-table driven (D10): ``_TOOL_ASSEMBLY`` pairs each domain
``ToolSpec`` with its application-layer builder. Adding a tool pack means one
spec entry plus one builder — no imperative branching inside ``_build``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from pydantic_core import to_jsonable_python

from app.application.execution.tool_catalog import CatalogSnapshot, ToolDefinition
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
from app.domain.models.inference import ResolvedInferenceModel
from app.domain.models.resource_bindings import ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.models.session_mode import SessionMode
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.artifact import ArtifactTool
from app.domain.services.tools.ask_user import AskUserTool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.domain.services.tools.errors import ToolInvocationError
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.image_generation import ImageGenerationTool
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.memory import MemoryTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.tool_registry import ToolRegistry
from app.domain.services.tools.tool_specs import AGENT_ONLY, ALL_MODES, ToolSpec
from app.domain.services.tools.vision import VisionTool
from app.domain.services.tools.vision_grounding import VisionGroundingTool
from app.domain.vector_port import EmbeddingPort

logger = logging.getLogger(__name__)


@dataclass
class _AssemblySite:
    """Per-build dependency bundle consumed by the spec builders."""

    payload: dict[str, JsonValue]
    context: ActivityContext
    mode: SessionMode
    scope: OwnerScope
    skill: Skill | None
    session_id: str
    bindings: list[dict[str, str]]
    sandbox: Sandbox
    browser: object
    model: ResolvedInferenceModel | None
    vision_llm: LLM | None
    rerank_llm: LLM | None


_Builder = Callable[["AgentToolCatalog", _AssemblySite], Awaitable[Sequence[BaseTool]]]


@dataclass(frozen=True)
class _ToolAssembly:
    spec: ToolSpec
    build: _Builder


@dataclass
class _BuiltCatalog:
    packs: list[BaseTool]
    # (kind, pack, retrieval tool name) triples selected by ToolSpec.retrieval_tool.
    retrieval: list[tuple[str, BaseTool, str]] = field(default_factory=list)
    fingerprint: str = ""


async def _assemble_mcp(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    mcp = MCPTool(catalog._mcp_pool)
    await mcp.initialize(
        await catalog._mcp_servers.resolve_mcp_runtime(
            site.scope,
            server_refs=(tuple(site.skill.mcp_server_refs) if site.skill else None),
        ),
        policy=site.context.run.policy_snapshot.common.activity,
    )
    return [mcp]


async def _assemble_a2a(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    a2a = A2ATool(catalog._a2a_pool)
    await a2a.initialize(
        await catalog._a2a_servers.resolve_a2a_runtime(
            site.scope,
            server_refs=(tuple(site.skill.a2a_server_refs) if site.skill else None),
        ),
        policy=site.context.run.policy_snapshot.common.activity,
    )
    return [a2a]


async def _assemble_search(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    # SEARCH_PROVIDER=none 时不注册搜索工具：显式缺席优于静默空结果。
    if catalog._search_engine is None:
        return []
    return [SearchTool(search_engine=catalog._search_engine)]


async def _assemble_file(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    return [FileTool(sandbox=site.sandbox)]


async def _assemble_shell(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    return [ShellTool(sandbox=site.sandbox)]


async def _assemble_browser(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    return [BrowserTool(browser=site.browser)]


async def _assemble_image_generation(
    catalog: AgentToolCatalog,
    site: _AssemblySite,
) -> Sequence[BaseTool]:
    assert site.model is not None
    return [
        ImageGenerationTool(
            inference_model=site.model,
            image_generator=catalog._image_generator,
            file_storage=catalog._file_storage,
            owner_user_id=site.context.owner_user_id,
            team_id=site.context.team_id,
        )
    ]


async def _assemble_vision(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    if site.vision_llm is None:
        return []
    return [
        VisionTool(sandbox=site.sandbox, llm=site.vision_llm),
        VisionGroundingTool(sandbox=site.sandbox, llm=site.vision_llm),
    ]


async def _assemble_ask_user(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    del catalog, site
    return [AskUserTool()]


async def _assemble_memory(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    return [catalog._memory_tool(session_id=site.session_id, context=site.context)]


async def _assemble_artifact(catalog: AgentToolCatalog, site: _AssemblySite) -> Sequence[BaseTool]:
    return [catalog._artifact_tool(session_id=site.session_id, sandbox=site.sandbox)]


async def _assemble_knowledge_base(
    catalog: AgentToolCatalog,
    site: _AssemblySite,
) -> Sequence[BaseTool]:
    family_policy = site.context.run.policy_snapshot.family_policy
    packs: list[BaseTool] = []
    for binding in site.bindings:
        if binding["resource_kind"] != ResourceKind.KNOWLEDGE_BASE.value:
            continue
        packs.append(
            KnowledgeBaseTool(
                uow_factory=catalog._uow_factory,
                kb_id=str(binding["resource_id"]),
                version_id=str(binding["version_id"]),
                policy=family_policy.knowledge_retrieval,
                llm=site.rerank_llm,
                embeddings=catalog._embeddings,
                owner_scope=site.scope,
            )
        )
    return packs


_TOOL_ASSEMBLY: tuple[_ToolAssembly, ...] = (
    _ToolAssembly(ToolSpec("mcp", modes=ALL_MODES), _assemble_mcp),
    _ToolAssembly(ToolSpec("a2a", modes=ALL_MODES), _assemble_a2a),
    _ToolAssembly(ToolSpec("search", modes=AGENT_ONLY), _assemble_search),
    _ToolAssembly(ToolSpec("file", modes=AGENT_ONLY), _assemble_file),
    _ToolAssembly(ToolSpec("shell", modes=AGENT_ONLY), _assemble_shell),
    _ToolAssembly(ToolSpec("browser", modes=AGENT_ONLY), _assemble_browser),
    _ToolAssembly(
        ToolSpec("image_generation", modes=AGENT_ONLY),
        _assemble_image_generation,
    ),
    _ToolAssembly(ToolSpec("vision", modes=AGENT_ONLY), _assemble_vision),
    _ToolAssembly(ToolSpec("memory", modes=AGENT_ONLY), _assemble_memory),
    # 澄清选项卡片：模型需求不清时经审批等待机制向用户发起选择（AGENT 专属，
    # ASK 模式只读问答不暂停）。
    _ToolAssembly(ToolSpec("ask_user", modes=AGENT_ONLY), _assemble_ask_user),
    _ToolAssembly(ToolSpec("artifact", modes=AGENT_ONLY), _assemble_artifact),
    _ToolAssembly(
        ToolSpec("knowledge_base", modes=ALL_MODES, retrieval_tool="kb_search"),
        _assemble_knowledge_base,
    ),
)


class AgentToolCatalog:
    """Single fail-closed source for model exposure and tool execution."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        sandbox_factory: SandboxFactoryPort,
        search_engine: SearchEngine | None,
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
    ) -> CatalogSnapshot:
        built = await self._build(payload, context)
        try:
            return CatalogSnapshot(
                definitions=tuple(
                    ToolDefinition(
                        name=descriptor.name,
                        tool_schema=descriptor.schema,
                        requires_approval=descriptor.policy.requires_approval(),
                        risk_summary=(f"{descriptor.policy.effect.value}: {descriptor.name}"),
                        approval_kind=descriptor.policy.approval_kind,
                        approval_prompt_param=descriptor.policy.approval_prompt_param,
                        approval_choices_param=descriptor.policy.approval_choices_param,
                    )
                    for pack in built.packs
                    for descriptor in pack.get_tool_descriptors()
                ),
                fingerprint=built.fingerprint,
            )
        finally:
            await _cleanup(built.packs)

    async def invoke(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        name: str,
        arguments: dict[str, JsonValue],
        expected_fingerprint: str | None = None,
        approval_feedback: str | None = None,
    ) -> dict[str, JsonValue]:
        built = await self._build(payload, context)
        try:
            if expected_fingerprint and expected_fingerprint != built.fingerprint:
                # 目录漂移本身不失败（D9）：仅当请求的工具已消失/被禁时
                # 才返回 not_found tool error 喂回模型。
                logger.warning(
                    "tool catalog drifted since the model decision (expected %s, got %s)",
                    expected_fingerprint[:12],
                    built.fingerprint[:12],
                )
            matches = [
                pack
                for pack in built.packs
                if any(descriptor.name == name for descriptor in pack.get_tool_descriptors())
            ]
            if len(matches) != 1:
                raise ToolInvocationError(
                    f"工具[{name}]已不可用（不存在、被禁用或注册歧义）",
                    kind="not_found",
                )
            # Reviewer feedback flows only into tools that declare a receiving
            # parameter (approval_feedback_param) — data-driven, never keyed on
            # tool names. For ask_user this is the user's chosen option.
            descriptor = next(
                item for item in matches[0].get_tool_descriptors() if item.name == name
            )
            feedback_param = descriptor.policy.approval_feedback_param
            if approval_feedback and feedback_param:
                arguments = {**arguments, feedback_param: approval_feedback}
            try:
                result = await matches[0].invoke(name, **arguments)
            except asyncio.CancelledError:
                try:
                    await matches[0].on_cancel()
                except (AttributeError, OSError, RuntimeError, ValueError):
                    logger.warning("tool on_cancel hook failed for %s", name)
                raise
            encoded = to_jsonable_python(result)
            if not isinstance(encoded, dict):
                raise TypeError("tool result must serialize to an object")
            return encoded
        finally:
            await _cleanup(built.packs)

    async def retrieve(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        query: str,
    ) -> dict[str, JsonValue]:
        built = await self._build(payload, context)
        try:
            sources: list[JsonValue] = []
            for kind, pack, retrieval_tool in built.retrieval:
                result = await pack.invoke(retrieval_tool, query=query)
                sources.append(
                    {
                        "kind": kind,
                        "result": to_jsonable_python(result),
                    }
                )
            return {"query": query, "sources": sources}
        finally:
            await _cleanup(built.packs)

    async def _build(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
    ) -> _BuiltCatalog:
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
        model: ResolvedInferenceModel | None = None
        vision_llm: LLM | None = None
        if mode == SessionMode.AGENT:
            model_id = payload.get("model_id")
            if model_id is not None and not isinstance(model_id, str):
                raise ValueError("model_id must be a string")
            model = await self._models.resolve_chat(model_id, scope=scope)
            if self._llm_factory is not None and model.model.capabilities.vision:
                vision_llm = self._llm_factory(
                    model,
                    policy=context.run.policy_snapshot.common.model_resilience,
                    thinking_enabled=False,
                    inference_model_service=self._models,
                    scope=scope,
                )
        rerank_llm = await self._rerank_llm(
            payload,
            context=context,
            scope=scope,
            bindings=bindings,
        )
        site = _AssemblySite(
            payload=payload,
            context=context,
            mode=mode,
            scope=scope,
            skill=skill,
            session_id=session_id,
            bindings=bindings,
            sandbox=sandbox,
            browser=browser,
            model=model,
            vision_llm=vision_llm,
            rerank_llm=rerank_llm,
        )
        policy = CapabilityPolicy.for_mode(
            mode,
            allowed_tool_names=(skill.allowed_tools if skill else None),
        )
        packs: list[BaseTool] = []
        retrieval: list[tuple[str, BaseTool, str]] = []
        for assembly in _TOOL_ASSEMBLY:
            if mode not in assembly.spec.modes:
                continue
            for raw_pack in await assembly.build(self, site):
                pack = ToolRegistry.build_tools(
                    policy=policy,
                    candidate_tools=[raw_pack],
                )[0]
                packs.append(pack)
                if assembly.spec.retrieval_tool is not None and pack.has_tool(
                    assembly.spec.retrieval_tool
                ):
                    retrieval.append((assembly.spec.name, pack, assembly.spec.retrieval_tool))
        return _BuiltCatalog(
            packs=packs,
            retrieval=retrieval,
            fingerprint=_catalog_fingerprint(packs, skill=skill, mode=mode),
        )

    async def _rerank_llm(
        self,
        payload: dict[str, JsonValue],
        *,
        context: ActivityContext,
        scope: OwnerScope,
        bindings: list[dict[str, str]],
    ) -> LLM | None:
        family_policy = context.run.policy_snapshot.family_policy
        if (
            self._llm_factory is None
            or not family_policy.knowledge_retrieval.rerank.enabled
            or not any(b["resource_kind"] == ResourceKind.KNOWLEDGE_BASE.value for b in bindings)
        ):
            return None
        rerank_model_id = payload.get("model_id")
        if rerank_model_id is not None and not isinstance(rerank_model_id, str):
            return None
        try:
            rerank_model = await self._models.resolve_chat(rerank_model_id, scope=scope)
            return self._llm_factory(
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
            return None

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
            # 运行中 Run 引用的 skill 被禁用：与 model.call 一致地降级为
            # "无 skill 继续"（P2-10），不再击穿 Run。通知链复杂，选择
            # warning 日志 + model.call public_data 提示字段的实现。
            logger.warning(
                "skill %s is disabled; continuing without skill authorization",
                skill_id,
            )
            return None
        return skill

    def _memory_tool(self, *, session_id: str, context: ActivityContext) -> BaseTool:
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

        return MemoryTool(save_fn=save_memory, session_id=session_id)

    def _artifact_tool(self, *, session_id: str, sandbox: Sandbox) -> BaseTool:
        async def write_artifact(**kwargs):
            content = kwargs.get("content") or ""
            source_path = kwargs.get("source_path")
            if source_path:
                result = await sandbox.read_file(source_path, max_length=None)
                if not result.success or not isinstance(result.data, dict):
                    raise ToolInvocationError(
                        "artifact source file is unavailable",
                        kind="execution_failed",
                    )
                content = result.data.get("content")
            if not isinstance(content, str) or not content:
                raise ToolInvocationError(
                    "artifact content is required",
                    kind="invalid_arguments",
                )
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

        return ArtifactTool(
            write_fn=write_artifact,
            finalize_fn=finalize_artifact,
        )


def _catalog_fingerprint(
    packs: list[BaseTool],
    *,
    skill: Skill | None,
    mode: SessionMode,
) -> str:
    entries = sorted(
        (
            {
                "name": descriptor.name,
                "pack": descriptor.tool_pack,
                "policy": descriptor.policy.model_dump(mode="json"),
            }
            for pack in packs
            for descriptor in pack.get_tool_descriptors()
        ),
        key=lambda entry: (entry["name"], entry["pack"]),
    )
    payload = {
        "mode": mode.value,
        "skill": (
            {
                "id": skill.id,
                "updated_at": skill.updated_at.isoformat(),
                "allowed_tools": skill.allowed_tools,
            }
            if skill
            else None
        ),
        "tools": entries,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


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

    async def cleanup(self) -> None:
        """取消传播：仅在页面已实际打开时关闭，不为关闭而新建浏览器。"""
        if self._browser is None:
            return
        cleanup = getattr(self._browser, "cleanup", None)
        if cleanup is not None:
            await cleanup()
        self._browser = None

    def __getattr__(self, name: str):
        async def call(*args, **kwargs):
            browser = await self._resolve()
            return await getattr(browser, name)(*args, **kwargs)

        return call


__all__ = ["AgentToolCatalog"]
