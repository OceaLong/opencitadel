import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.migrate_runtime_policy_seed import seed_runtime_policy_heads
from core.config import DeploymentSettings


@pytest.fixture
def client(_db_schema):
    settings = DeploymentSettings(env="test")
    asyncio.run(seed_runtime_policy_heads(settings))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_get_status(client: TestClient) -> None:
    """测试获取应用状态API接口"""
    # 1.使用客户端请求获取数据
    response = client.get("/api/status")
    data = response.json()

    # 2.断言状态码和业务状态码
    assert response.status_code == 200
    assert data.get("code") == 200
