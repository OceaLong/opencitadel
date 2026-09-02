"""Exact retained greenfield API route inventory."""

from fastapi import APIRouter, Depends

from app.interfaces.auth_dependencies import enforce_auditor_read_only

from . import (
    admin_routes,
    approval_routes,
    artifact_routes,
    auth_routes,
    file_routes,
    governance_policy_routes,
    inference_routes,
    integration_routes,
    knowledge_base_routes,
    metrics_routes,
    notification_routes,
    run_routes,
    status_routes,
    team_routes,
)


def create_api_routes() -> APIRouter:
    router = APIRouter()
    router.include_router(auth_routes.router)
    router.include_router(status_routes.health_router)
    router.include_router(status_routes.router)
    router.include_router(metrics_routes.router)

    authenticated = APIRouter(dependencies=[Depends(enforce_auditor_read_only)])
    authenticated.include_router(run_routes.router)
    authenticated.include_router(approval_routes.router)
    authenticated.include_router(admin_routes.router)
    authenticated.include_router(governance_policy_routes.router)
    authenticated.include_router(inference_routes.router)
    authenticated.include_router(integration_routes.router)
    authenticated.include_router(file_routes.router)
    authenticated.include_router(artifact_routes.router)
    authenticated.include_router(knowledge_base_routes.router)
    authenticated.include_router(team_routes.router)
    authenticated.include_router(team_routes.invitation_router)
    authenticated.include_router(notification_routes.router)
    router.include_router(authenticated)
    return router


router = create_api_routes()

__all__ = ["create_api_routes", "router"]
