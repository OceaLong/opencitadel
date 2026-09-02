from __future__ import annotations

from types import SimpleNamespace

import docker
import httpx
import pytest

from app.infrastructure.external.sandbox.broker import create_broker_app
from core.config import DeploymentSettings


class FakeContainer:
    def __init__(self, name: str, config: dict[str, object]) -> None:
        self.name = name
        self.status = "running"
        self.config = config
        self.attrs = {
            "Config": {"Labels": config["labels"]},
            "NetworkSettings": {"Networks": {"sandbox-net": {"IPAddress": "172.30.0.9"}}},
            "State": {"StartedAt": "2026-09-02T00:00:00Z"},
        }

    def reload(self) -> None:
        return None

    def remove(self, *, force: bool) -> None:
        assert force is True


class FakeContainers:
    def __init__(self) -> None:
        self.items: dict[str, FakeContainer] = {}

    def get(self, name: str) -> FakeContainer:
        try:
            return self.items[name]
        except KeyError as exc:
            raise docker.errors.NotFound("missing") from exc

    def run(self, **config: object) -> FakeContainer:
        container = FakeContainer(str(config["name"]), config)
        self.items[container.name] = container
        return container


@pytest.mark.asyncio
async def test_broker_creates_only_authenticated_policy_bounded_sandboxes() -> None:
    containers = FakeContainers()
    settings = DeploymentSettings(
        sandbox_broker_token="b" * 32,
        sandbox_image="opencitadel-sandbox",
        sandbox_name_prefix="opencitadel-sandbox",
        sandbox_network="sandbox-net",
    )
    app = create_broker_app(
        settings,
        docker_factory=lambda: SimpleNamespace(containers=containers),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        denied = await client.post("/v1/sandboxes", json={})
        assert denied.status_code == 401

        response = await client.post(
            "/v1/sandboxes",
            headers={"Authorization": f"Bearer {'b' * 32}"},
            json={
                "id": "opencitadel-sandbox-deadbeef",
                "operations_revision_id": "0df433ac-4d71-56ee-a523-4fe047fc0661",
                "policy": {
                    "ttl_minutes": 60,
                    "memory_limit": "2g",
                    "cpu_limit": 2.0,
                    "pids_limit": 512,
                },
                "access_token": "run-token",
            },
        )

    assert response.status_code == 200
    assert response.json()["ip"] == "172.30.0.9"
    config = containers.items["opencitadel-sandbox-deadbeef"].config
    assert config["read_only"] is True
    assert config["cap_drop"] == ["ALL"]
    assert config["mem_limit"] == "2g"
    assert config["environment"]["SANDBOX_ACCESS_TOKEN"] == "run-token"
