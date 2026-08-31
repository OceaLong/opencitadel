import hashlib
import json
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_collector_validator import MCPPatrolCollectorValidator
from app.domain.models.integration_server import MCPServerRecord
from app.domain.runtime_policy import ActivityExecutionPolicy


class Manager:
    connection_errors: ClassVar[dict] = {}

    def __init__(self):
        names = [
            "get_capabilities",
            "k8s_workload_summary",
            "k8s_recent_events",
            "prom_query",
            "certificate_status",
            "backup_status",
            "dependency_status",
            "http_probe",
        ]
        self.tools = {"collector": [SimpleNamespace(name=name) for name in names]}
        self.calls = []

    async def get_all_tools(self):
        return [
            {"function": {"name": f"mcp_collector_{item.name}"}} for item in self.tools["collector"]
        ]

    async def invoke(self, name, arguments):
        self.calls.append((name, arguments))
        if name.endswith("get_capabilities"):
            payload = {"enabled_tools": [item.name for item in self.tools["collector"]]}
        else:
            payload = {
                "status": "ok",
                "request_id": name,
                "target_ref": "opencitadel-local",
                "data": {},
                "evidence": [
                    {
                        "type": "summary",
                        "ref": f"collector://evidence/{name}/summary",
                        "sha256": hashlib.sha256(b"{}").hexdigest(),
                        "target_ref": "opencitadel-local",
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ],
            }
        return SimpleNamespace(success=True, data=json.dumps(payload), message="")


@pytest.mark.asyncio
async def test_live_validator_calls_only_manifest_tools_with_pack_arguments():
    manager = Manager()
    pool = SimpleNamespace(acquire=AsyncMock(return_value=manager))
    validator = MCPPatrolCollectorValidator(pool)
    server = MCPServerRecord(id="server-1", name="collector", url="https://collector.example/mcp")
    config = load_patrol_template("kubernetes-baseline-v1")

    policy = ActivityExecutionPolicy()
    capabilities = await validator.get_capabilities(server, policy=policy)
    dry_run = await validator.dry_run(server, config, policy=policy)

    assert "get_capabilities" in capabilities["enabled_tools"]
    assert dry_run["ok"] is True
    assert len(dry_run["probes"]) == 9
    assert not any(
        {"url", "promql", "command", "script"} & set(arguments) for _, arguments in manager.calls
    )


@pytest.mark.asyncio
async def test_collect_preserves_the_current_collector_evidence_contract() -> None:
    manager = Manager()
    pool = SimpleNamespace(acquire=AsyncMock(return_value=manager))
    validator = MCPPatrolCollectorValidator(pool)
    server = MCPServerRecord(id="server-1", name="collector", url="https://collector.example/mcp")
    config = load_patrol_template("compose-services-baseline-v1")
    config.checks = config.checks[:1]

    submissions = await validator.collect(
        server,
        config,
        policy=ActivityExecutionPolicy(),
    )

    assert len(submissions) == 1
    assert [item.model_dump(mode="json") for item in submissions[0].evidence_refs] == [
        {
            "type": "summary",
            "ref": "collector://evidence/mcp_collector_http_probe/summary",
            "sha256": hashlib.sha256(b"{}").hexdigest(),
            "target_ref": "opencitadel-local",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    ]
