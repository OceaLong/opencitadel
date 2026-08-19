"""Codebase version/build route regressions."""
import json
from datetime import datetime, timezone

import pytest

from app.application.dto.codebase_build import (
    CodebaseBuildProjection,
    CodebaseVersionHistoryProjection,
    CodebaseVersionProjection,
)
from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    CodebaseArtifact,
)
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.resource_governance import BuildState
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints import codebase_routes
from app.interfaces.schemas.codebase import ReadSourceRequest


def _ctx() -> WorkspaceContext:
    return WorkspaceContext(
        principal=Principal(user_id="u1"),
        scope=OwnerScope.personal("u1"),
    )


def _build() -> CodebaseBuildProjection:
    return CodebaseBuildProjection(
        id="build-1",
        codebase_id="cb1",
        version_id="candidate",
        parent_version_id="active",
        command_key="reanalyze:cb1",
        state=BuildState.RUNNING,
        phase="analyze",
        progress=0.5,
        created_at=datetime.now(timezone.utc),
        can_cancel=True,
    )


def _version(build=None) -> CodebaseVersionProjection:
    return CodebaseVersionProjection(
        id="candidate",
        codebase_id="cb1",
        parent_version_id="active",
        build_id="build-1",
        state=CodebaseVersionState.BUILDING,
        capabilities={"source_read": True},
        metrics={
            "unsupported_views": {
                "flowchart": "unsupported",
            }
        },
        created_at=datetime.now(timezone.utc),
        is_candidate=True,
        build=build,
    )


def test_version_response_serializes_nested_immutable_metrics():
    response = codebase_routes._to_version_response(_version(_build()))

    payload = json.loads(response.model_dump_json())

    assert payload["metrics"]["unsupported_views"] == {
        "flowchart": "unsupported",
    }


@pytest.mark.asyncio
async def test_version_build_routes_forward_exact_owner_scoped_identities():
    ctx = _ctx()
    calls = []
    build = _build()
    version = _version(build)

    class Service:
        async def list_versions(self, codebase_id, **kwargs):
            calls.append(("list", codebase_id, kwargs["scope"]))
            return CodebaseVersionHistoryProjection(
                codebase_id=codebase_id,
                active_version_id="active",
                active_build=build,
                versions=[version],
            )

        async def get_version(self, codebase_id, version_id, **kwargs):
            calls.append(("get", codebase_id, version_id, kwargs["scope"]))
            return version

        async def create_build(self, codebase_id, **kwargs):
            calls.append(("create", codebase_id, kwargs["scope"]))
            return version

        async def retry_build(self, codebase_id, build_id, **kwargs):
            calls.append(("retry", codebase_id, build_id, kwargs["scope"]))
            return version

        async def cancel_build(self, codebase_id, build_id, **kwargs):
            calls.append(("cancel", codebase_id, build_id, kwargs["scope"]))
            return build

    service = Service()
    await codebase_routes.list_codebase_versions("cb1", ctx, service)
    await codebase_routes.get_codebase_version("cb1", "candidate", ctx, service)
    create_response = await codebase_routes.create_codebase_build(
        "cb1",
        ctx,
        Principal(user_id="u1"),
        service,
    )
    await codebase_routes.retry_codebase_build(
        "cb1",
        "build-1",
        ctx,
        Principal(user_id="u1"),
        service,
    )
    await codebase_routes.cancel_codebase_build(
        "cb1",
        "build-1",
        ctx,
        Principal(user_id="u1"),
        service,
    )

    assert create_response.data.build.id == "build-1"
    assert calls == [
        ("list", "cb1", ctx.scope),
        ("get", "cb1", "candidate", ctx.scope),
        ("create", "cb1", ctx.scope),
        ("retry", "cb1", "build-1", ctx.scope),
        ("cancel", "cb1", "build-1", ctx.scope),
    ]


@pytest.mark.asyncio
async def test_version_source_and_artifacts_routes_use_requested_version():
    ctx = _ctx()
    calls = []
    artifact = CodebaseArtifact(
        id="a1",
        codebase_id="cb1",
        version_id="candidate",
        kind=ArtifactKind.CALL_CHAIN,
        format=ArtifactFormat.MERMAID,
        title="Call chain",
        content="graph LR",
        meta={"edges": [{"evidence_refs": [{"path": "src/main.py"}]}]},
    )

    class Service:
        async def read_source(self, codebase_id, path, **kwargs):
            calls.append(
                (
                    "source",
                    codebase_id,
                    path,
                    kwargs["codebase_version_id"],
                    kwargs["scope"],
                )
            )
            return "line"

        async def list_artifacts(self, codebase_id, **kwargs):
            calls.append(
                (
                    "artifacts",
                    codebase_id,
                    kwargs["kind"],
                    kwargs["codebase_version_id"],
                    kwargs["scope"],
                )
            )
            return [artifact]

    service = Service()
    source = await codebase_routes.read_version_source(
        "cb1",
        "candidate",
        ReadSourceRequest(path="src/main.py", start_line=1, end_line=1),
        ctx,
        service,
        object(),
    )
    artifacts = await codebase_routes.list_version_artifacts(
        "cb1",
        "candidate",
        ArtifactKind.CALL_CHAIN,
        ctx,
        service,
    )

    assert source.data.content == "line"
    assert artifacts.data.artifacts[0].meta["edges"][0]["evidence_refs"]
    assert calls == [
        ("source", "cb1", "src/main.py", "candidate", ctx.scope),
        ("artifacts", "cb1", ArtifactKind.CALL_CHAIN, "candidate", ctx.scope),
    ]
