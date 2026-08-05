"""Owner-scoped Ops Patrol HTTP endpoints."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from app.application.errors.exceptions import BadRequestError
from app.application.services.patrol_evidence_service import PatrolEvidenceService
from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_pack_service import PatrolPackService
from app.application.services.patrol_remediation_service import (
    PatrolRemediationService,
    _allowed_actions_for_probe_tool,
)
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolFinding,
    PatrolFindingStatus,
    PatrolPackConfig,
    PatrolRun,
)
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.patrol import (
    CreatePatrolPackRequest,
    FindingDecisionRequest,
    PatrolCheckResultResponse,
    PatrolFindingResponse,
    PatrolPackListResponse,
    PatrolPackMetricsResponse,
    PatrolPackResponse,
    PatrolRemediationListResponse,
    PatrolRemediationResponse,
    PatrolRunDetailResponse,
    PatrolRunListResponse,
    PatrolRunResponse,
    ProposePatrolRemediationRequest,
    UpdatePatrolPackRequest,
)
from app.interfaces.service_dependencies import (
    get_patrol_evidence_service,
    get_patrol_pack_service,
    get_patrol_remediation_service,
    get_patrol_run_service,
)


router = APIRouter(tags=["Ops Patrol"])


def _finding_allowed_actions(
    finding: PatrolFinding, check_results: list[PatrolCheckResult], run: PatrolRun
) -> list[str]:
    """Resolve which remediation actions a Finding's probe tool supports.

    Assembly point for `PatrolFindingResponse.allowed_actions` — computed
    here (not on the domain model) because it depends on the Run's pack
    config snapshot, which isn't part of the Finding row. Reuses
    `patrol_remediation_service._allowed_actions_for_probe_tool` so the
    read-side classification never drifts from `propose()`'s write-side
    enforcement of the same rule.
    """
    check_result = next((item for item in check_results if item.id == finding.check_result_id), None)
    if check_result is None:
        return []
    config = PatrolPackConfig.model_validate(run.pack_snapshot["config"])
    check = next((item for item in config.checks if item.id == check_result.check_id), None)
    if check is None:
        return []
    return sorted(action.value for action in _allowed_actions_for_probe_tool(check.probe.tool))


@router.get("/patrol-packs", response_model=ApiResponse[PatrolPackListResponse])
async def list_packs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: PatrolPackService = Depends(get_patrol_pack_service),
):
    packs = await service.list_packs(ctx.scope, limit=limit, offset=offset)
    return ApiResponse.success(PatrolPackListResponse(items=[PatrolPackResponse.from_domain(item) for item in packs], limit=limit, offset=offset))


@router.post("/patrol-packs", response_model=ApiResponse[PatrolPackResponse])
async def create_pack(
    body: CreatePatrolPackRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: PatrolPackService = Depends(get_patrol_pack_service),
):
    pack = await service.create_pack(
        owner_user_id=ctx.principal.user_id,
        scope=ctx.scope,
        name=body.name,
        mcp_server_id=body.mcp_server_id,
        config=(load_patrol_template(body.template_id or "kubernetes-baseline-v1", body.config) if body.template_id or not body.config else PatrolPackConfig.model_validate(body.config)),
    )
    return ApiResponse.success(PatrolPackResponse.from_domain(pack))


@router.get("/patrol-packs/{pack_id}", response_model=ApiResponse[PatrolPackResponse])
async def get_pack(pack_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), service: PatrolPackService = Depends(get_patrol_pack_service)):
    return ApiResponse.success(PatrolPackResponse.from_domain(await service.get_pack(pack_id, ctx.scope)))


@router.get("/patrol-packs/{pack_id}/metrics", response_model=ApiResponse[PatrolPackMetricsResponse])
async def get_pack_metrics(
    pack_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: PatrolRunService = Depends(get_patrol_run_service),
):
    return ApiResponse.success(
        PatrolPackMetricsResponse(**(await service.get_pack_metrics(pack_id, ctx.scope)))
    )


@router.patch("/patrol-packs/{pack_id}", response_model=ApiResponse[PatrolPackResponse])
async def update_pack(
    pack_id: str,
    body: UpdatePatrolPackRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: PatrolPackService = Depends(get_patrol_pack_service),
):
    pack = await service.patch_pack(
        pack_id,
        ctx.scope,
        ctx.principal.user_id,
        expected_version=body.version,
        name=body.name,
        config=body.config,
        mcp_server_id=body.mcp_server_id,
    )
    return ApiResponse.success(PatrolPackResponse.from_domain(pack))


@router.delete("/patrol-packs/{pack_id}", response_model=ApiResponse[dict])
async def delete_pack(pack_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolPackService = Depends(get_patrol_pack_service)):
    await service.delete_pack(pack_id, ctx.scope, ctx.principal.user_id)
    return ApiResponse.success({"deleted": True})


@router.post("/patrol-packs/{pack_id}/validate", response_model=ApiResponse[PatrolPackResponse])
async def validate_pack(pack_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolPackService = Depends(get_patrol_pack_service)):
    return ApiResponse.success(PatrolPackResponse.from_domain(await service.validate_pack(pack_id, ctx.scope, ctx.principal.user_id)))


@router.post("/patrol-packs/{pack_id}/activate", response_model=ApiResponse[PatrolPackResponse])
async def activate_pack(pack_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolPackService = Depends(get_patrol_pack_service)):
    return ApiResponse.success(PatrolPackResponse.from_domain(await service.activate_pack(pack_id, ctx.scope, ctx.principal.user_id)))


@router.post("/patrol-packs/{pack_id}/pause", response_model=ApiResponse[PatrolPackResponse])
async def pause_pack(pack_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolPackService = Depends(get_patrol_pack_service)):
    return ApiResponse.success(PatrolPackResponse.from_domain(await service.pause_pack(pack_id, ctx.scope, ctx.principal.user_id)))


@router.post("/patrol-packs/{pack_id}/trigger", response_model=ApiResponse[PatrolRunResponse])
async def trigger_pack(
    pack_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: PatrolRunService = Depends(get_patrol_run_service),
):
    if not (idempotency_key or "").strip():
        raise BadRequestError("Idempotency-Key is required", error_key="patrol.idempotencyRequired")
    return ApiResponse.success(PatrolRunResponse.from_domain(await service.trigger_pack(pack_id, ctx.scope, ctx.principal.user_id, idempotency_key=idempotency_key)))


@router.get("/patrol-runs", response_model=ApiResponse[PatrolRunListResponse])
async def list_runs(
    pack_id: str | None = None,
    status: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: PatrolRunService = Depends(get_patrol_run_service),
):
    runs = await service.list_runs(ctx.scope, pack_id=pack_id, status=status, created_from=created_from, created_to=created_to, limit=limit, offset=offset)
    return ApiResponse.success(PatrolRunListResponse(items=[PatrolRunResponse.from_domain(item) for item in runs], limit=limit, offset=offset))


@router.get("/patrol-runs/{run_id}", response_model=ApiResponse[PatrolRunDetailResponse])
async def get_run(run_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), service: PatrolRunService = Depends(get_patrol_run_service)):
    run, results, findings = await service.get_run_detail(
        run_id,
        ctx.scope,
        review_actor_user_id=None if ctx.principal.is_auditor else ctx.principal.user_id,
    )
    return ApiResponse.success(PatrolRunDetailResponse(
        **PatrolRunResponse.from_domain(run).model_dump(),
        check_results=[PatrolCheckResultResponse.from_domain(item) for item in results],
        findings=[
            PatrolFindingResponse.from_domain(item, allowed_actions=_finding_allowed_actions(item, results, run))
            for item in findings
        ],
    ))


@router.get("/patrol-runs/{run_id}/evidence")
async def download_evidence(run_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), service: PatrolEvidenceService = Depends(get_patrol_evidence_service)):
    payload = await service.build_package(run_id, ctx.scope, actor_user_id=ctx.principal.user_id)
    headers = {"Content-Disposition": f'attachment; filename="patrol-{run_id}.zip"', "X-Content-Type-Options": "nosniff"}
    return StreamingResponse(iter([payload]), media_type="application/zip", headers=headers)


@router.post("/patrol-runs/{run_id}/cancel", response_model=ApiResponse[PatrolRunResponse])
async def cancel_run(run_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolRunService = Depends(get_patrol_run_service)):
    return ApiResponse.success(PatrolRunResponse.from_domain(await service.cancel_run(run_id, ctx.scope, ctx.principal.user_id)))


@router.post("/patrol-runs/{run_id}/replay", response_model=ApiResponse[PatrolRunResponse])
async def replay_run(run_id: str, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolRunService = Depends(get_patrol_run_service)):
    return ApiResponse.success(PatrolRunResponse.from_domain(await service.replay_run(run_id, ctx.scope, ctx.principal.user_id)))


async def _decide(finding_id: str, status: PatrolFindingStatus, body: FindingDecisionRequest, ctx: WorkspaceContext, service: PatrolRunService):
    finding = await service.decide_finding(finding_id, ctx.scope, ctx.principal.user_id, status, body.reason)
    run, results, _findings = await service.get_run_detail(finding.run_id, ctx.scope)
    allowed_actions = _finding_allowed_actions(finding, results, run)
    return ApiResponse.success(PatrolFindingResponse.from_domain(finding, allowed_actions=allowed_actions))


@router.post("/patrol-findings/{finding_id}/acknowledge", response_model=ApiResponse[PatrolFindingResponse])
async def acknowledge_finding(finding_id: str, body: FindingDecisionRequest, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolRunService = Depends(get_patrol_run_service)):
    return await _decide(finding_id, PatrolFindingStatus.ACKNOWLEDGED, body, ctx, service)


@router.post("/patrol-findings/{finding_id}/resolve", response_model=ApiResponse[PatrolFindingResponse])
async def resolve_finding(finding_id: str, body: FindingDecisionRequest, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolRunService = Depends(get_patrol_run_service)):
    return await _decide(finding_id, PatrolFindingStatus.RESOLVED, body, ctx, service)


@router.post("/patrol-findings/{finding_id}/false-positive", response_model=ApiResponse[PatrolFindingResponse])
async def false_positive_finding(finding_id: str, body: FindingDecisionRequest, ctx: WorkspaceContext = Depends(get_workspace_context), _write_guard=Depends(require_non_auditor), service: PatrolRunService = Depends(get_patrol_run_service)):
    return await _decide(finding_id, PatrolFindingStatus.FALSE_POSITIVE, body, ctx, service)


@router.post("/patrol-findings/{finding_id}/remediations", response_model=ApiResponse[PatrolRemediationResponse])
async def propose_remediation(
    finding_id: str,
    body: ProposePatrolRemediationRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: PatrolRemediationService = Depends(get_patrol_remediation_service),
):
    remediation = await service.propose(finding_id, body.action, body.params, ctx.scope, ctx.principal.user_id, workload=body.workload)
    return ApiResponse.success(PatrolRemediationResponse.from_domain(remediation))


@router.get("/patrol-runs/{run_id}/remediations", response_model=ApiResponse[PatrolRemediationListResponse])
async def list_remediations(
    run_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: PatrolRemediationService = Depends(get_patrol_remediation_service),
):
    items = await service.list_for_run(run_id, ctx.scope)
    return ApiResponse.success(PatrolRemediationListResponse(items=[PatrolRemediationResponse.from_domain(item) for item in items]))


@router.get("/patrol-remediations/{remediation_id}", response_model=ApiResponse[PatrolRemediationResponse])
async def get_remediation(
    remediation_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: PatrolRemediationService = Depends(get_patrol_remediation_service),
):
    return ApiResponse.success(PatrolRemediationResponse.from_domain(await service.get(remediation_id, ctx.scope)))
