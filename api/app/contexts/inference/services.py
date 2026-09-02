"""Inference configuration and OpenAI-compatible invocation adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.crypto import VersionedSecretCipher
from app.domain.errors import ForbiddenError, NotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.kernel.domain.types import OwnerScopeRef
from app.kernel.infrastructure.postgres.models import KernelRunORM
from app.kernel.infrastructure.postgres.session_auth import bind_context

from .models import (
    InferenceBindingORM,
    InferenceEndpointORM,
    InferenceModelORM,
    InferenceUsageORM,
    MCPServerORM,
)


def _scope_values(scope: OwnerScope) -> dict[str, str | None]:
    return {
        "owner_user_id": None if scope.team_id else scope.user_id,
        "team_id": scope.team_id,
    }


def _endpoint_view(row: InferenceEndpointORM) -> dict[str, object]:
    return {
        "id": row.id,
        "displayName": row.display_name,
        "provider": row.provider,
        "baseUrl": row.base_url,
        "visibility": row.visibility,
        "hasCredential": bool(row.credential_ciphertext),
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


def _model_view(row: InferenceModelORM) -> dict[str, object]:
    return {
        "id": row.id,
        "endpointId": row.endpoint_id,
        "displayName": row.display_name,
        "modelName": row.model_name,
        "kind": row.kind,
        "settings": row.settings,
        "capabilities": row.capabilities,
        "visibility": row.visibility,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


class PostgresInferenceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        cipher: VersionedSecretCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    async def list_endpoints(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (
                await session.scalars(
                    select(InferenceEndpointORM).order_by(InferenceEndpointORM.created_at)
                )
            ).all()
        return [_endpoint_view(row) for row in rows]

    async def put_endpoint(
        self,
        endpoint_id: str | None,
        value: dict[str, object],
        *,
        scope: OwnerScope,
        is_admin: bool,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        identifier = endpoint_id or str(uuid4())
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(InferenceEndpointORM, identifier)
            credential = str(value.get("credential") or "")
            if row is None:
                visibility = (
                    "global" if is_admin and value.get("visibility") == "global" else "private"
                )
                owner = (
                    {"owner_user_id": None, "team_id": None}
                    if visibility == "global"
                    else _scope_values(scope)
                )
                row = InferenceEndpointORM(
                    id=identifier,
                    display_name=str(value["displayName"]),
                    provider=str(value.get("provider") or "openai"),
                    base_url=str(value.get("baseUrl") or "https://api.openai.com/v1"),
                    credential_ciphertext=self._cipher.encrypt_versioned(credential),
                    credential_encryption="fernet_v2",
                    visibility=visibility,
                    created_at=now,
                    updated_at=now,
                    **owner,
                )
                session.add(row)
            else:
                row.display_name = str(value["displayName"])
                row.provider = str(value.get("provider") or row.provider)
                row.base_url = str(value.get("baseUrl") or row.base_url)
                if credential:
                    row.credential_ciphertext = self._cipher.encrypt_versioned(credential)
                row.updated_at = now
        return _endpoint_view(row)

    async def delete_endpoint(self, endpoint_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(InferenceEndpointORM, endpoint_id)
            if row is None:
                raise NotFoundError("Inference endpoint not found")
            await session.delete(row)

    async def list_models(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (
                await session.scalars(
                    select(InferenceModelORM).order_by(InferenceModelORM.created_at)
                )
            ).all()
        return [_model_view(row) for row in rows]

    async def put_model(
        self,
        model_id: str | None,
        value: dict[str, object],
        *,
        scope: OwnerScope,
        is_admin: bool,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        identifier = model_id or str(uuid4())
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(InferenceModelORM, identifier)
            if row is None:
                visibility = (
                    "global" if is_admin and value.get("visibility") == "global" else "private"
                )
                owner = (
                    {"owner_user_id": None, "team_id": None}
                    if visibility == "global"
                    else _scope_values(scope)
                )
                row = InferenceModelORM(
                    id=identifier,
                    endpoint_id=str(value["endpointId"]),
                    display_name=str(value["displayName"]),
                    model_name=str(value["modelName"]),
                    kind=str(value.get("kind") or "chat"),
                    settings=dict(value.get("settings") or {}),
                    capabilities=dict(value.get("capabilities") or {}),
                    visibility=visibility,
                    created_at=now,
                    updated_at=now,
                    **owner,
                )
                session.add(row)
            else:
                row.endpoint_id = str(value["endpointId"])
                row.display_name = str(value["displayName"])
                row.model_name = str(value["modelName"])
                row.kind = str(value.get("kind") or row.kind)
                row.settings = dict(value.get("settings") or {})
                row.capabilities = dict(value.get("capabilities") or {})
                row.updated_at = now
        return _model_view(row)

    async def delete_model(self, model_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(InferenceModelORM, model_id)
            if row is None:
                raise NotFoundError("Inference model not found")
            await session.delete(row)

    async def list_bindings(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (await session.scalars(select(InferenceBindingORM))).all()
        return [
            {
                "id": str(row.id),
                "purpose": row.purpose,
                "modelId": row.model_id,
                "scopeType": row.scope_type,
                "scopeKey": row.scope_key,
            }
            for row in rows
        ]

    async def set_binding(
        self,
        purpose: str,
        model_id: str,
        *,
        scope: OwnerScope,
        scope_type: str = "current",
        is_admin: bool = False,
    ) -> dict[str, object]:
        if scope_type == "global":
            if not is_admin:
                raise ForbiddenError("Administrator permission required for a global binding")
            resolved_scope_type = "global"
            scope_key = "global"
            owner = {"owner_user_id": None, "team_id": None}
        elif scope_type == "current":
            resolved_scope_type = "team" if scope.team_id else "user"
            scope_key = scope.team_id or scope.user_id
            owner = _scope_values(scope)
        else:
            raise ValueError("unsupported inference binding scope")
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.scalar(
                select(InferenceBindingORM).where(
                    InferenceBindingORM.scope_type == resolved_scope_type,
                    InferenceBindingORM.scope_key == scope_key,
                    InferenceBindingORM.purpose == purpose,
                )
            )
            if row is None:
                row = InferenceBindingORM(
                    id=uuid4(),
                    scope_type=resolved_scope_type,
                    scope_key=scope_key,
                    purpose=purpose,
                    model_id=model_id,
                    created_at=now,
                    updated_at=now,
                    **owner,
                )
                session.add(row)
            else:
                row.model_id = model_id
                row.updated_at = now
        return {
            "id": str(row.id),
            "purpose": row.purpose,
            "modelId": row.model_id,
            "scopeType": row.scope_type,
            "scopeKey": row.scope_key,
        }

    async def list_mcp(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            await bind_context(session)
            rows = (await session.scalars(select(MCPServerORM))).all()
        return [self._mcp_view(row) for row in rows]

    async def put_mcp(
        self,
        server_id: str | None,
        value: dict[str, object],
        *,
        scope: OwnerScope,
        is_admin: bool,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        identifier = server_id or str(uuid4())
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(MCPServerORM, identifier)
            config = dict(value.get("config") or {})
            if row is None:
                visibility = (
                    "global" if is_admin and value.get("visibility") == "global" else "private"
                )
                owner = (
                    {"owner_user_id": None, "team_id": None}
                    if visibility == "global"
                    else _scope_values(scope)
                )
                row = MCPServerORM(
                    id=identifier,
                    name=str(value["name"]),
                    transport=str(value.get("transport") or "streamable_http"),
                    config_ciphertext=self._cipher.encrypt_versioned(
                        json.dumps(config, sort_keys=True)
                    ),
                    secret_encryption="fernet_v2",
                    capability_catalog=dict(value.get("capabilityCatalog") or {}),
                    visibility=visibility,
                    created_at=now,
                    updated_at=now,
                    **owner,
                )
                session.add(row)
            else:
                row.name = str(value["name"])
                row.transport = str(value.get("transport") or row.transport)
                if config:
                    row.config_ciphertext = self._cipher.encrypt_versioned(
                        json.dumps(config, sort_keys=True)
                    )
                row.capability_catalog = dict(value.get("capabilityCatalog") or {})
                row.updated_at = now
        return self._mcp_view(row)

    async def delete_mcp(self, server_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            row = await session.get(MCPServerORM, server_id)
            if row is None:
                raise NotFoundError("MCP server not found")
            await session.delete(row)

    @staticmethod
    def _mcp_view(row: MCPServerORM) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "transport": row.transport,
            "capabilityCatalog": row.capability_catalog,
            "visibility": row.visibility,
            "hasSecretConfig": bool(row.config_ciphertext),
        }


class OpenAIInferenceGateway:
    """Resolve a frozen binding and execute a model call under a system scope."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        cipher: VersionedSecretCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    async def invoke(
        self, request: dict[str, object], *, idempotency_key: str
    ) -> dict[str, object]:
        scope = OwnerScopeRef.model_validate(request["_owner_scope"])
        run_id = UUID(str(request["_run_id"]))
        async with self._session_factory() as session:
            await bind_context(session, AuthorizationContext.system("model-effect"))
            binding = await session.scalar(
                select(InferenceBindingORM).where(
                    InferenceBindingORM.purpose == "agent",
                    InferenceBindingORM.scope_type == ("team" if scope.team_id else "user"),
                    InferenceBindingORM.scope_key == (scope.team_id or scope.owner_user_id),
                )
            )
            if binding is None:
                binding = await session.scalar(
                    select(InferenceBindingORM).where(
                        InferenceBindingORM.purpose == "agent",
                        InferenceBindingORM.scope_type == "global",
                    )
                )
            if binding is None:
                raise RuntimeError("no Agent inference binding configured")
            model = await session.get(InferenceModelORM, binding.model_id)
            endpoint = (
                await session.get(InferenceEndpointORM, model.endpoint_id)
                if model is not None
                else None
            )
            if model is None or endpoint is None:
                raise RuntimeError("inference binding target is incomplete")
            run = await session.get(KernelRunORM, run_id)
            if run is None:
                raise RuntimeError("model Effect references a missing Run")
            api_key = self._cipher.decrypt_versioned(endpoint.credential_ciphertext)
        client = AsyncOpenAI(api_key=api_key, base_url=endpoint.base_url)
        prompt = str(request.get("prompt") or "")
        if request.get("tool_result") is not None:
            prompt = f"Continue using this tool result: {request['tool_result']}"
        retrieval_matches = list(request.get("retrieval_matches") or [])
        if retrieval_matches:
            prompt = f"{prompt}\n\nUse only when relevant. Retrieved knowledge:\n" + json.dumps(
                retrieval_matches, ensure_ascii=False
            )
        catalog = list(request.get("tool_catalog") or [])
        tools = [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item.get("description", ""),
                    "parameters": item.get("input_schema", {"type": "object"}),
                },
            }
            for item in catalog
        ]
        result = await client.chat.completions.create(
            model=model.model_name,
            messages=[{"role": "user", "content": prompt}],
            tools=tools or None,
            extra_headers={"Idempotency-Key": idempotency_key},
            **model.settings,
        )
        message = result.choices[0].message
        tool_calls = [
            {
                "name": item.function.name,
                "arguments": json.loads(item.function.arguments or "{}"),
            }
            for item in (message.tool_calls or [])
        ]
        usage = result.usage
        usage_value = {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
        }
        async with self._session_factory() as session, session.begin():
            await bind_context(session, AuthorizationContext.system("model-usage-recorder"))
            await session.execute(
                insert(InferenceUsageORM)
                .values(
                    invocation_id=UUID(idempotency_key),
                    run_id=run_id,
                    actor_user_id=run.created_by_user_id,
                    model_id=model.id,
                    input_tokens=usage_value["input_tokens"],
                    output_tokens=usage_value["output_tokens"],
                    owner_user_id=scope.owner_user_id,
                    team_id=scope.team_id,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=[InferenceUsageORM.invocation_id])
            )
        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "finish_reason": result.choices[0].finish_reason,
            "usage": usage_value,
        }


__all__ = ["OpenAIInferenceGateway", "PostgresInferenceService"]
