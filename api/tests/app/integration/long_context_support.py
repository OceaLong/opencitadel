"""Controlled tools and HTTP model responses; all kernel transitions remain real."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.application.execution.agent_tool_catalog import AgentToolCatalog, _BuiltCatalog
from app.application.services.runtime_policy_service import RuntimePolicyService
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import READ_SAFE
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.endpoints.runtime_policy_routes import router
from app.interfaces.service_dependencies import get_runtime_policy_service


class ReadEvidence(BaseTool):
    name = "read"

    def __init__(self):
        super().__init__()
        self.calls = []

    @tool(
        name="read_evidence",
        description="Read one bounded evidence item",
        parameters={"round": {"type": "integer"}, "item": {"type": "integer"}},
        required=["round", "item"],
        policy=READ_SAFE,
    )
    async def read_evidence(self, round, item):
        self.calls.append((round, item))
        return ToolResult(
            success=True,
            data={
                "round": round,
                "item": item,
                "fact": round * 17 + item,
                "evidence": "immutable read evidence " * 25,
            },
        )


def tool_catalog(uow, *, source_reader=None):
    pack = ReadEvidence()
    catalog = object.__new__(AgentToolCatalog)
    catalog._uow_factory = uow
    catalog._contract_capture = catalog._replay = catalog._isolated = None

    async def build(_payload, context):
        packs = [pack]
        if source_reader is not None:
            from app.application.execution.context_read_tool import ContextReadTool

            packs.append(
                ContextReadTool(lambda **kwargs: source_reader.read_for_activity(context, **kwargs))
            )
        return _BuiltCatalog(packs=packs, fingerprint="controlled-read-evidence-v1")

    catalog._build = build
    return catalog, pack


class ModelResponses:
    def __init__(self, rounds, *, original_source=None, original_round=0, ask=False):
        self.rounds = rounds
        self.original_source = original_source
        self.original_round = original_round
        self.ask = ask
        self.main, self.summary = [], []

    def message(self, wire):
        if wire["max_completion_tokens"] == 1024:
            self.summary.append(wire)
            ids = []
            for message in wire["messages"]:
                prefix = "Source identities for the following evidence: "
                if message.get("content", "").startswith(prefix):
                    ids.extend(message["content"][len(prefix) :].split(", "))
            assert ids
            return {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "facts": [
                            {
                                "kind": "fact",
                                "text": "Read results retained; no external writes were performed.",
                                "source_ids": [ids[0]],
                            }
                        ]
                    }
                ),
            }
        ordinal = len(self.main)
        self.main.append(wire)
        if self.ask:
            assert not wire.get("tools")
            return {"role": "assistant", "content": f"Accepted evidence turn {ordinal}."}
        if ordinal == self.rounds:
            assert not wire.get("tools")
            return {"role": "assistant", "content": "All requested evidence reads completed."}
        assert ordinal < self.rounds
        assert wire.get("tools")
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"read-{ordinal}-{item}",
                    "type": "function",
                    "function": {
                        "name": "read_evidence",
                        "arguments": json.dumps({"round": ordinal, "item": item}),
                    },
                }
                for item in range(2)
            ],
        }
        if self.original_source is not None and ordinal == self.original_round:
            message["tool_calls"][1]["function"] = {
                "name": "context_read_source",
                "arguments": json.dumps({"source_id": self.original_source, "limit": 8192}),
            }
        return message


async def activate_iterations(policies, principal, scope, iterations):
    pair = await policies.load_active_pair()
    policy = pair.execution.revision.policy.model_copy(
        update={
            "agent": pair.execution.revision.policy.agent.model_copy(
                update={"max_iterations": iterations}
            ),
        }
    )

    async def sink(*args, **kwargs):
        pass

    service = RuntimePolicyService(
        repository=policies,
        audit_service=SimpleNamespace(record=sink),
        hint_publisher=SimpleNamespace(publish_changed=sink),
    )
    app = FastAPI()
    app.include_router(router)
    # Authentication is the isolated fixture boundary, not a cap or policy stub.
    app.dependency_overrides[require_admin] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: SimpleNamespace(
        principal=principal, scope=scope
    )
    app.dependency_overrides[get_runtime_policy_service] = lambda: service
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://owned-test"
    ) as client:
        response = await client.post(
            "/runtime-policies/execution/revisions",
            json={
                "policy": policy.model_dump(mode="json"),
                "expected_head_version": pair.execution.head.version,
                "expected_active_revision_id": str(pair.execution.revision.id),
                "note": "Owned long-task acceptance",
            },
        )
        assert response.status_code == 200, response.text
    return await policies.load_active_pair()
