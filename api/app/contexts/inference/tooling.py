"""Frozen tool catalogs and worker adapters for the retained tool surface."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.crypto import OutboundNetworkPolicy, VersionedSecretCipher
from app.contexts.identity.models import GovernancePolicyHeadORM, GovernancePolicyRevisionORM
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.integration_runtime import MCPRuntime, MCPServerRuntime, MCPTransport
from app.domain.models.tool_policy import ToolExecutionPolicy
from app.domain.runtime_policy.governance import GovernancePolicy
from app.domain.utils.outbound_url import parse_allowed_ports
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser
from app.infrastructure.external.sandbox.kubernetes import KubernetesSandboxManager
from app.infrastructure.external.sandbox.token import derive_sandbox_token
from app.infrastructure.external.tools.mcp_client import MCPClientManager, build_mcp_tool_name
from app.kernel.domain.types import EffectSafety, OwnerScopeRef
from app.kernel.infrastructure.postgres.session_auth import bind_context
from core.config import DeploymentSettings

from .models import MCPServerORM


def _capability(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
    safety: EffectSafety = EffectSafety.READ_ONLY,
    approval: bool = False,
    effect_type: str = "tool.call",
    result_fields: tuple[str, ...] = ("status", "data", "message"),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return {
        "name": name,
        "description": description,
        "input_schema": schema,
        "safety": safety.value,
        "requires_approval": approval,
        "effect_type": effect_type,
        "result_fields": list(result_fields),
    }


BUILTIN_TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    _capability(
        "file.read",
        "Read a UTF-8 file from this Run's isolated workspace.",
        {"path": {"type": "string"}},
        required=("path",),
        effect_type="file.operation",
    ),
    _capability(
        "file.list",
        "List files below a directory in this Run's isolated workspace.",
        {"path": {"type": "string", "default": "."}},
        effect_type="file.operation",
    ),
    _capability(
        "file.write",
        "Write a UTF-8 file in this Run's isolated workspace.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        required=("path", "content"),
        safety=EffectSafety.IDEMPOTENT_WRITE,
        approval=True,
        effect_type="file.operation",
    ),
    _capability(
        "file.delete",
        "Delete a file in this Run's isolated workspace.",
        {"path": {"type": "string"}},
        required=("path",),
        safety=EffectSafety.IDEMPOTENT_WRITE,
        approval=True,
        effect_type="file.operation",
    ),
    _capability(
        "shell.run",
        "Run one shell command inside this Run's sandbox.",
        {
            "command": {"type": "string"},
            "cwd": {"type": "string", "default": "."},
        },
        required=("command",),
        safety=EffectSafety.NON_IDEMPOTENT_WRITE,
        approval=True,
    ),
    _capability("browser.view", "Read the current browser page.", {}),
    _capability(
        "browser.navigate",
        "Navigate the sandbox browser to an HTTP(S) URL.",
        {"url": {"type": "string"}},
        required=("url",),
    ),
    _capability(
        "browser.click",
        "Click a visible browser element by index.",
        {"index": {"type": "integer"}},
        required=("index",),
        safety=EffectSafety.NON_IDEMPOTENT_WRITE,
        approval=True,
    ),
    _capability(
        "browser.input",
        "Enter text into a visible browser element.",
        {
            "index": {"type": "integer"},
            "text": {"type": "string"},
            "press_enter": {"type": "boolean", "default": False},
        },
        required=("index", "text"),
        safety=EffectSafety.NON_IDEMPOTENT_WRITE,
        approval=True,
    ),
    _capability(
        "browser.scroll",
        "Scroll the current browser page.",
        {"direction": {"type": "string", "enum": ["up", "down"]}},
        required=("direction",),
    ),
    _capability(
        "browser.screenshot",
        "Capture a browser screenshot as base64.",
        {"full_page": {"type": "boolean", "default": False}},
        result_fields=("status", "image_base64"),
    ),
)


def _visible_mcp_filter(scope: OwnerScopeRef):
    owner = (
        MCPServerORM.team_id == scope.team_id
        if scope.team_id
        else MCPServerORM.owner_user_id == scope.owner_user_id
    )
    return or_(MCPServerORM.visibility == "global", owner)


def _mcp_capabilities(row: MCPServerORM) -> list[dict[str, Any]]:
    catalog = dict(row.capability_catalog or {})
    raw_tools = catalog.get("tools", [])
    if isinstance(raw_tools, dict):
        raw_tools = [{"name": key, **dict(value)} for key, value in raw_tools.items()]
    if not isinstance(raw_tools, list):
        return []
    values: list[dict[str, Any]] = []
    for raw in raw_tools:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        source_name = str(raw["name"]).strip()
        values.append(
            {
                "name": build_mcp_tool_name(row.id, source_name),
                "description": str(raw.get("description") or source_name),
                "input_schema": dict(
                    raw.get("inputSchema") or raw.get("input_schema") or {"type": "object"}
                ),
                "safety": str(raw.get("safety") or EffectSafety.NON_IDEMPOTENT_WRITE.value),
                "requires_approval": bool(raw.get("requiresApproval", True)),
                "effect_type": "tool.call",
                "result_fields": list(raw.get("resultFields") or ("status", "data", "message")),
                "server_id": row.id,
                "source_name": source_name,
            }
        )
    return values


class PostgresToolCatalog:
    """Resolve and freeze the exact tools visible when a Run starts."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def for_scope(self, scope: OwnerScopeRef) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (
                await session.scalars(
                    select(MCPServerORM).where(_visible_mcp_filter(scope)).order_by(MCPServerORM.id)
                )
            ).all()
            head = await session.get(GovernancePolicyHeadORM, 1)
            revision = (
                await session.get(GovernancePolicyRevisionORM, head.revision_id)
                if head is not None
                else None
            )
        policy = (
            GovernancePolicy.model_validate(revision.policy)
            if revision is not None
            else GovernancePolicy()
        )
        candidates = [dict(value) for value in BUILTIN_TOOL_CATALOG]
        for row in rows:
            candidates.extend(_mcp_capabilities(row))
        allowed = set(policy.tool_allowlist)
        denied = set(policy.tool_denylist)
        return [
            value
            for value in candidates
            if value["name"] not in denied and (not allowed or value["name"] in allowed)
        ]


class SandboxRuntime:
    """Lazily resolves one deterministic, broker-owned sandbox per Run."""

    def __init__(self, settings: DeploymentSettings) -> None:
        self._settings = settings
        self._resolved: dict[str, tuple[str, str, dict[str, str]]] = {}
        self._browsers: dict[str, PlaywrightBrowser] = {}
        self._lock = asyncio.Lock()
        self._kubernetes = KubernetesSandboxManager(settings)

    async def _endpoint(self, run_id: str) -> tuple[str, str, dict[str, str]]:
        cached = self._resolved.get(run_id)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._resolved.get(run_id)
            if cached is not None:
                return cached
            address = self._settings.sandbox_address.strip()
            if address:
                host = urlsplit(address if "://" in address else f"http://{address}").hostname
                if not host:
                    raise RuntimeError("SANDBOX_ADDRESS is invalid")
                value = (f"http://{host}:8080", f"http://{host}:9222", {})
                self._resolved[run_id] = value
                return value
            broker_url = self._settings.sandbox_broker_url.strip().rstrip("/")
            prefix = self._settings.sandbox_name_prefix.strip()
            if not prefix:
                raise RuntimeError("sandbox broker or fixed sandbox address is required")
            sandbox_id = f"{prefix}-{UUID(run_id).hex[:8]}"
            token = derive_sandbox_token(self._settings.sandbox_token_seed, sandbox_id)
            if self._settings.sandbox_driver.strip().lower() == "kubernetes":
                ip = await self._kubernetes.endpoint(sandbox_id, token)
            else:
                if not broker_url:
                    raise RuntimeError("sandbox broker or fixed sandbox address is required")
                broker_headers = {"Authorization": f"Bearer {self._settings.sandbox_broker_token}"}
                async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                    response = await client.get(
                        f"{broker_url}/v1/sandboxes/{sandbox_id}", headers=broker_headers
                    )
                    if response.status_code == 404:
                        response = await client.post(
                            f"{broker_url}/v1/sandboxes",
                            headers=broker_headers,
                            json={
                                "id": sandbox_id,
                                "operations_revision_id": str(
                                    uuid5(NAMESPACE_URL, "opencitadel-kernel-v2-sandbox-policy")
                                ),
                                "policy": {
                                    "ttl_minutes": 60,
                                    "memory_limit": "2g",
                                    "cpu_limit": 2.0,
                                    "pids_limit": 512,
                                },
                                "access_token": token,
                            },
                        )
                    response.raise_for_status()
                    ip = str(response.json().get("ip") or "").strip()
            if not ip:
                raise RuntimeError("sandbox broker returned no address")
            data_headers = {"Authorization": f"Bearer {token}"}
            value = (f"http://{ip}:8080", f"http://{ip}:9222", data_headers)
            self._resolved[run_id] = value
            return value

    async def post(self, run_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        base_url, _, headers = await self._endpoint(run_id)
        try:
            async with httpx.AsyncClient(timeout=600, trust_env=False) as client:
                response = await client.post(f"{base_url}{path}", headers=headers, json=body)
                response.raise_for_status()
                payload = dict(response.json())
        except (httpx.HTTPError, ValueError):
            self._resolved.pop(run_id, None)
            browser = self._browsers.pop(run_id, None)
            if browser is not None:
                await browser.cleanup()
            raise
        if int(payload.get("code", 500)) >= 300:
            raise RuntimeError(str(payload.get("msg") or "sandbox operation failed"))
        return {
            "status": "completed",
            "data": payload.get("data"),
            "message": str(payload.get("msg") or ""),
        }

    async def browser(self, run_id: str) -> PlaywrightBrowser:
        browser = self._browsers.get(run_id)
        if browser is not None:
            return browser
        _, cdp_url, headers = await self._endpoint(run_id)
        browser = PlaywrightBrowser(cdp_url, cdp_headers=headers)
        self._browsers[run_id] = browser
        return browser

    async def close(self) -> None:
        browsers = list(self._browsers.values())
        self._browsers.clear()
        for browser in browsers:
            await browser.cleanup()
        await self._kubernetes.close()


def _sandbox_path(run_id: str, value: object) -> str:
    path = PurePosixPath(str(value or "."))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("sandbox file path must be relative to the Run workspace")
    return str(PurePosixPath("/home/ubuntu/workspaces") / UUID(run_id).hex / path)


class SandboxFileGateway:
    def __init__(self, sandbox: SandboxRuntime) -> None:
        self._sandbox = sandbox

    async def operate(
        self,
        request: dict[str, Any],
        *,
        run_id: str,
        idempotency_key: str,
    ) -> bytes:
        del idempotency_key
        operation = str(request.get("operation") or "")
        path = _sandbox_path(run_id, request.get("path"))
        if operation == "read":
            result = await self._sandbox.post(
                run_id,
                "/api/file/read-file",
                {"filepath": path, "max_length": 50_000, "sudo": False},
            )
            return str(result.get("data") or "").encode()
        if operation == "write":
            content = str(request.get("content") or "")
            await self._sandbox.post(
                run_id,
                "/api/file/write-file",
                {
                    "filepath": path,
                    "content": content,
                    "append": False,
                    "leading_newline": False,
                    "trailing_newline": False,
                    "sudo": False,
                },
            )
            return content.encode()
        if operation == "delete":
            result = await self._sandbox.post(run_id, "/api/file/delete-file", {"filepath": path})
            return json.dumps(result, sort_keys=True).encode()
        if operation == "list":
            result = await self._sandbox.post(
                run_id,
                "/api/file/find-files",
                {"dir_path": path, "glob_pattern": "*"},
            )
            return json.dumps(result.get("data"), sort_keys=True).encode()
        raise ValueError("unsupported file operation")


class PostgresToolGateway:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        cipher: VersionedSecretCipher,
        settings: DeploymentSettings,
        sandbox: SandboxRuntime,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._settings = settings
        self._sandbox = sandbox

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        capability: dict[str, Any],
        run_id: str,
        owner_scope: OwnerScopeRef,
        idempotency_key: str,
    ) -> dict[str, Any]:
        del owner_scope, idempotency_key
        if name == "shell.run":
            return await self._sandbox.post(
                run_id,
                "/api/shell/exec-command",
                {
                    "session_id": f"run-{UUID(run_id).hex[:12]}",
                    "exec_dir": _sandbox_path(run_id, arguments.get("cwd")),
                    "command": str(arguments.get("command") or ""),
                },
            )
        if name.startswith("browser."):
            return await self._browser_call(name, arguments, run_id)
        if name.startswith("mcp_"):
            return await self._mcp_call(name, arguments, capability)
        raise LookupError(f"unsupported governed tool: {name}")

    async def _browser_call(
        self, name: str, arguments: dict[str, Any], run_id: str
    ) -> dict[str, Any]:
        browser = await self._sandbox.browser(run_id)
        if name == "browser.view":
            result = await browser.view_page()
        elif name == "browser.navigate":
            result = await browser.navigate(str(arguments.get("url") or ""))
        elif name == "browser.click":
            result = await browser.click(index=int(arguments["index"]))
        elif name == "browser.input":
            result = await browser.input(
                str(arguments.get("text") or ""),
                bool(arguments.get("press_enter", False)),
                index=int(arguments["index"]),
            )
        elif name == "browser.scroll":
            result = (
                await browser.scroll_up()
                if arguments.get("direction") == "up"
                else await browser.scroll_down()
            )
        elif name == "browser.screenshot":
            raw = await browser.screenshot(bool(arguments.get("full_page", False)))
            return {"status": "completed", "image_base64": base64.b64encode(raw).decode()}
        else:
            raise LookupError(f"unsupported browser tool: {name}")
        if not result.success:
            raise RuntimeError(result.message or "browser operation failed")
        return {"status": "completed", "data": result.data, "message": result.message or ""}

    async def _mcp_call(
        self,
        name: str,
        arguments: dict[str, Any],
        capability: dict[str, Any],
    ) -> dict[str, Any]:
        server_id = str(capability.get("server_id") or "")
        source_name = str(capability.get("source_name") or "")
        if not server_id or not source_name or build_mcp_tool_name(server_id, source_name) != name:
            raise ValueError("MCP call does not match its frozen capability")
        async with self._session_factory() as session:
            await bind_context(session, AuthorizationContext.system("mcp-effect"))
            row = await session.get(MCPServerORM, server_id)
        if row is None:
            raise RuntimeError("frozen MCP server no longer exists")
        config = json.loads(self._cipher.decrypt_versioned(row.config_ciphertext))
        raw_policies = dict(config.get("toolPolicies") or {})
        server = MCPServerRuntime(
            id=row.id,
            name=row.id,
            transport=MCPTransport(row.transport),
            command=config.get("command"),
            args=tuple(config.get("args") or ()) or None,
            url=config.get("url"),
            headers=dict(config.get("headers") or {}) or None,
            env=dict(config.get("env") or {}) or None,
            transport_options=dict(config.get("transportOptions") or {}),
            tool_policies={
                key: ToolExecutionPolicy.model_validate(value)
                for key, value in raw_policies.items()
            },
        )
        manager = MCPClientManager(
            MCPRuntime(servers={row.id: server}),
            connect_timeout=timedelta(seconds=30),
            tool_timeout=timedelta(seconds=300),
            outbound_policy=OutboundNetworkPolicy(
                allowed_ports=parse_allowed_ports(self._settings.outbound_allowed_ports),
                allow_private_hosts=frozenset(
                    value.strip()
                    for value in self._settings.outbound_private_host_allowlist.split(",")
                    if value.strip()
                ),
            ),
        )
        try:
            await manager.initialize()
            await manager.get_all_tools()
            result = await manager.invoke(name, arguments)
        finally:
            await manager.cleanup()
        if not result.success:
            raise RuntimeError(result.message or "MCP operation failed")
        return {"status": "completed", "data": result.data, "message": result.message or ""}


__all__ = [
    "BUILTIN_TOOL_CATALOG",
    "PostgresToolCatalog",
    "PostgresToolGateway",
    "SandboxFileGateway",
    "SandboxRuntime",
]
