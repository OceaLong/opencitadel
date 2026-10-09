"""Explicit registered test HTTP execution. No discovery, pool or connector fallback."""

import uuid

import httpx

from app.domain.evaluation.recording import validate_typed


class RegisteredHTTPTestExecutor:
    def __init__(self, target, *, credential_resolver=None, transport=None):
        if target.kind not in {"mcp", "a2a"} or not target.endpoint.startswith("http"):
            raise ValueError("test_http_executor_kind_invalid")
        self.target, self.credential_resolver, self.transport = (
            target,
            credential_resolver,
            transport,
        )

    def bind(self, adapter, lease):
        from app.infrastructure.adapters.evaluation_sandbox import LeaseTargetTransport

        return RegisteredHTTPTestExecutor(
            self.target,
            credential_resolver=self.credential_resolver,
            transport=LeaseTargetTransport(adapter, lease, self.target.endpoint, self.target.id),
        )

    def validate(self, target):
        expected = {"mcp": "mcp-stateless-json-2025-03-26", "a2a": "a2a-jsonrpc-0.3"}[target.kind]
        if target.protocol != expected:
            raise ValueError("test_target_protocol_unsupported")
        if target != self.target:
            raise ValueError("test_executor_binding_changed")

    async def invoke(self, target, credentials, scope, principal, name, arguments):
        self.validate(target)
        if self.transport is None:
            raise ValueError("test_lease_transport_required")
        contracts = [contract for contract in target.contracts if contract.name == name]
        if len(contracts) != 1:
            raise ValueError("test_tool_unregistered")
        contract = contracts[0]
        validate_typed(arguments, contract.arguments_schema)
        headers = {}
        for credential in credentials:
            if credential.target.id != target.id:
                continue
            if self.credential_resolver is None:
                raise ValueError("test_credential_resolver_unavailable")
            value = await self.credential_resolver.resolve(scope, principal, target, credential)
            if set(value) - {"Authorization", "X-Test-Token"}:
                raise ValueError("test_credential_header_forbidden")
            headers.update(value)
        async with httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
            timeout=30,
            headers=headers,
        ) as client:
            if target.kind == "mcp":
                initialize = {
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "opencitadel-controlled-evaluation", "version": "1"},
                    },
                }
                init = await client.post(
                    target.endpoint,
                    json=initialize,
                    headers={"Accept": "application/json, text/event-stream"},
                )
                if (
                    init.is_redirect
                    or init.headers.get("Mcp-Session-Id")
                    or "text/event-stream" in init.headers.get("content-type", "")
                ):
                    raise ValueError("test_mcp_transport_unsupported")
                init.raise_for_status()
                metadata = init.json()
                if (
                    metadata.get("id") != initialize["id"]
                    or metadata.get("result", {}).get("protocolVersion") != "2025-03-26"
                    or "tools" not in metadata.get("result", {}).get("capabilities", {})
                ):
                    raise ValueError("test_mcp_protocol_invalid")
                client.headers["MCP-Protocol-Version"] = "2025-03-26"
                client.headers["Accept"] = "application/json, text/event-stream"
                initialized = await client.post(
                    target.endpoint, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                )
                if initialized.is_redirect or initialized.status_code not in {200, 202, 204}:
                    raise ValueError("test_mcp_initialization_failed")
                # Only stateless JSON-response HTTP MCP test endpoints are registered.
                request = {
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "tools/call",
                    "params": {"name": contract.source_name, "arguments": arguments},
                }
            else:
                agent_id = arguments.get("id")
                if agent_id not in target.allowed_agent_ids:
                    raise ValueError("test_a2a_agent_denied")
                request = {
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "message/send",
                    "params": {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": arguments.get("query", "")}],
                        }
                    },
                }
            response = await client.post(target.endpoint, json=request)
            if response.is_redirect:
                raise ValueError("test_target_redirect_denied")
            response.raise_for_status()
            if len(response.content) > 20 * 1024 * 1024:
                raise ValueError("test_target_result_too_large")
            data = response.json()
            if "error" in data or data.get("id") != request["id"] or "result" not in data:
                raise ValueError("test_target_protocol_invalid")
            return {"success": True, "data": data["result"]}


class SyntheticTestCredentialResolver:
    async def resolve(self, scope, principal, target, credential):
        if (
            credential.locator != "synthetic-e04-v1"
            or credential.resolver != "synthetic-e04-v1"
            or credential.target.id != target.id
        ):
            raise ValueError("test_credential_binding_invalid")
        # Synthetic E04 fixture header; this resolver never imports deployment credentials.
        return {"X-Test-Token": "e04-owned-fixture-v1"}  # gitleaks:allow
