from datetime import datetime

from fastapi import APIRouter, Depends, Query
from starlette.responses import Response, StreamingResponse

from app.application.services.audit_service import AuditService
from app.application.services.compliance_service import ComplianceService
from app.application.services.evidence_service import EvidenceService
from app.application.services.governance_overview_service import GovernanceOverviewService
from app.application.services.governance_profile_service import GovernanceProfileService
from app.domain.errors import AppException, NotFoundError
from app.interfaces.auth_dependencies import require_auditor_or_admin
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.compliance import (
    ChainVerifyResponse,
    ComplianceReportResponse,
    EvidenceSessionItem,
    EvidenceSessionListResponse,
    GovernanceOverviewResponse,
)
from app.interfaces.service_dependencies import (
    get_audit_service,
    get_compliance_service,
    get_evidence_service,
    get_governance_overview_service,
    get_governance_profile_service,
)

router = APIRouter(prefix="/admin", tags=["合规证据"])


@router.get(
    "/audit/verify-chain",
    response_model=ApiResponse[ChainVerifyResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def verify_chain(
    audit_service: AuditService = Depends(get_audit_service),
):
    result = await audit_service.verify_chain()
    return ApiResponse.success(data=ChainVerifyResponse(**result))


@router.get(
    "/audit/verify-chain/sessions/{session_id}",
    response_model=ApiResponse[ChainVerifyResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def verify_session_chain(
    session_id: str,
    audit_service: AuditService = Depends(get_audit_service),
):
    result = await audit_service.verify_session_chain(session_id)
    return ApiResponse.success(data=ChainVerifyResponse(**result))


@router.get(
    "/evidence/sessions",
    response_model=ApiResponse[EvidenceSessionListResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def list_evidence_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: EvidenceService = Depends(get_evidence_service),
):
    items = await service.list_evidence_sessions(limit=limit, offset=offset)
    return ApiResponse.success(
        data=EvidenceSessionListResponse(sessions=[EvidenceSessionItem(**item) for item in items])
    )


@router.get(
    "/evidence/sessions/{session_id}/package",
    dependencies=[Depends(require_auditor_or_admin)],
)
async def download_evidence_package(
    session_id: str,
    service: EvidenceService = Depends(get_evidence_service),
):
    try:
        data = await service.build_session_evidence_package(session_id)
    except ValueError as exc:
        raise NotFoundError(
            str(exc), error_key="apiErrors.compliance.evidencePackageNotFound"
        ) from exc
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="evidence-{session_id}.zip"'},
    )


@router.get(
    "/compliance/report",
    dependencies=[Depends(require_auditor_or_admin)],
)
async def get_compliance_report(
    framework: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    format: str = Query("json", pattern="^(json|md|pdf)$"),
    service: ComplianceService = Depends(get_compliance_service),
):
    frameworks: list[str] | None = [framework] if framework else None
    report = await service.build_report(start_at=start, end_at=end, frameworks=frameworks)
    if format == "json":
        return ApiResponse.success(data=ComplianceReportResponse(report=report))
    if format == "md":
        return Response(
            content=service.render_markdown(report),
            media_type="text/markdown; charset=utf-8",
        )
    pdf = service.render_pdf(report)
    if pdf is None:
        raise AppException(
            code=501,
            status_code=501,
            msg="PDF rendering is unavailable; use the json or md format instead",
            error_key="apiErrors.compliance.pdfUnavailable",
        )
    return Response(content=pdf, media_type="application/pdf")


@router.get(
    "/governance/sessions/{session_id}/profile",
    dependencies=[Depends(require_auditor_or_admin)],
)
async def get_governance_profile(
    session_id: str,
    service: GovernanceProfileService = Depends(get_governance_profile_service),
):
    # Auditors and admins may inspect any session's governance evidence. This
    # endpoint is gated by require_auditor_or_admin, so cross-owner reads are
    # intentional here -- mirrors download_evidence_package, which likewise
    # passes scope=None. Using the caller's personal ctx.scope previously made
    # every cross-owner session return 404 (even for admins).
    profile = await service.build_profile(session_id, scope=None)
    return ApiResponse.success(data=profile)


@router.get(
    "/governance/overview",
    response_model=ApiResponse[GovernanceOverviewResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def get_governance_overview(
    days: int = Query(30, ge=1, le=365),
    service: GovernanceOverviewService = Depends(get_governance_overview_service),
):
    overview = await service.build_overview(days=days)
    return ApiResponse.success(data=GovernanceOverviewResponse(**overview))
