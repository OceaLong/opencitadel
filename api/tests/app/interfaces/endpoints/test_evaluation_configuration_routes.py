# ruff: noqa: F811
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_actual_configuration_routes_and_secret_free_version(configurations):
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_configuration_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_suite_service

    service, _ds, scope, principal = configurations
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_suite_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/evaluation/configs",
            json={
                "name": "HTTP",
                "request_id": str(uuid4()),
                "definition": {"model_id": "e02-model"},
            },
        )
        assert created.status_code == 200, created.text
        entity = created.json()["data"]["id"]
        published = await client.post(
            f"/evaluation/configs/{entity}/publish",
            json={"request_id": str(uuid4()), "expected_revision": 1},
        )
        assert published.status_code == 200, published.text
        value = published.json()["data"]
        assert value["version_unpinned"] is True
        assert "credential_ref" not in published.text
        assert "snapshot" not in published.text
        assert value["model_id"] == "e02-model"
        version = await client.get(f"/evaluation/configs/versions/{value['id']}")
        assert version.status_code == 200
        assert version.headers["cache-control"] == "no-store"
        listing = await client.get("/evaluation/configs/versions", params={"entity_id": entity})
        assert len(listing.json()["data"]["items"]) == 1
        for i in range(2):
            assert (
                await client.post(
                    "/evaluation/configs",
                    json={
                        "name": str(i),
                        "request_id": str(uuid4()),
                        "definition": {"model_id": "e02-model"},
                    },
                )
            ).status_code == 200
        first = (await client.get("/evaluation/configs", params={"limit": 1})).json()["data"]
        assert first["next_cursor"]
        second = (
            await client.get(
                "/evaluation/configs", params={"limit": 1, "cursor": first["next_cursor"]}
            )
        ).json()["data"]
        assert first["items"][0]["id"] != second["items"][0]["id"]
        assert (
            await client.get("/evaluation/rubrics", params={"cursor": first["next_cursor"]})
        ).status_code == 400
        assert (await client.get(f"/evaluation/configs/versions/{uuid4()}")).status_code == 404
        assert (
            await client.post(
                "/evaluation/configs",
                json={
                    "name": "Bad",
                    "request_id": str(uuid4()),
                    "definition": {"model_id": "e02-model", "override_base_rules": True},
                },
            )
        ).status_code == 400


async def test_e10_builtin_choices_are_current_scoped_static_and_mode_filtered(
    configurations, monkeypatch
):
    from sqlalchemy import text

    from app.application.evaluation.configuration_metadata import BUILTINS
    from app.domain.models.scope import OwnerScope, WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_configuration_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_suite_service
    from tests.app.execution_test_support import execution_admin_session

    service, _ds, scope, principal = configurations

    def forbidden(*args, **kwargs):
        raise AssertionError("tool constructor must not run")

    for cls in BUILTINS:
        monkeypatch.setattr(cls, "__init__", forbidden)
    from app.application.execution.agent_tool_catalog import AgentToolCatalog

    monkeypatch.setattr(AgentToolCatalog, "_build", forbidden)
    monkeypatch.setattr(AgentToolCatalog, "invoke", forbidden)
    from tests.app.application.services.test_artifact_provenance_postgres import seed

    foreign, _ = await seed()
    async with execution_admin_session() as db:
        for identity, owner in [("own-skill", principal.user_id), ("foreign-skill", foreign)]:
            await db.execute(
                text(
                    "INSERT INTO skills(id,slug,owner_user_id,visibility,allowed_tools) VALUES(:id,:id,:owner,'private','[\"read_file\"]'::jsonb)"
                ),
                {"id": identity, "owner": owner},
            )
        await db.commit()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_suite_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        agent = await client.get("/evaluation/config-options/tools?mode=agent")
        assert agent.status_code == 200, agent.text
        assert "read_file" in {v["name"] for v in agent.json()["data"]}
        assert all(set(v) == {"name", "mode"} for v in agent.json()["data"])
        filtered = await client.get(
            "/evaluation/config-options/tools?mode=agent&skill_id=own-skill"
        )
        assert filtered.status_code == 200, filtered.text
        assert [item["name"] for item in filtered.json()["data"]] == ["read_file"]
        ask = await client.get("/evaluation/config-options/tools?mode=ask")
        assert ask.status_code == 200
        assert "read_file" not in {v["name"] for v in ask.json()["data"]}
        assert (await client.get("/evaluation/config-options/tools?mode=bogus")).status_code == 400
        assert (
            await client.get("/evaluation/config-options/tools?mode=agent&skill_id=foreign-skill")
        ).status_code in (400, 404)
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=OwnerScope.personal("foreign"), principal=principal
        )
        assert (await client.get("/evaluation/config-options/tools")).status_code in (403, 404)
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=scope, principal=principal
        )
        from app.domain.models.scope import Principal
        from app.domain.models.user import GlobalRole

        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET global_role='auditor' WHERE id=:id"),
                {"id": principal.user_id},
            )
            await db.commit()
        principal = Principal(user_id=principal.user_id, global_role=GlobalRole.AUDITOR)
        assert (await client.get("/evaluation/config-options/tools")).status_code == 200
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET status='disabled' WHERE id=:id"), {"id": principal.user_id}
            )
            await db.commit()
        assert (await client.get("/evaluation/config-options/tools")).status_code == 403
