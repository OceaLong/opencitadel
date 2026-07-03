#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import Response, StreamingResponse

from app.application.services.audit_service import AuditService
from app.application.services.compliance_service import ComplianceService
from app.application.services.evidence_service import EvidenceService
from app.interfaces.auth_dependencies import require_auditor_or_admin
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.compliance import (
    ChainVerifyResponse,
    ComplianceReportResponse,
    EvidenceSessionItem,
    EvidenceSessionListResponse,
)
from app.interfaces.service_dependencies import (
    get_audit_service,
    get_compliance_service,
    get_evidence_service,
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
        data=EvidenceSessionListResponse(
            sessions=[EvidenceSessionItem(**item) for item in items]
        )
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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="evidence-{session_id}.zip"'
        },
    )


@router.get(
    "/compliance/report",
    dependencies=[Depends(require_auditor_or_admin)],
)
async def get_compliance_report(
    framework: Optional[str] = Query(None),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    format: str = Query("json", pattern="^(json|md|pdf)$"),
    service: ComplianceService = Depends(get_compliance_service),
):
    frameworks: Optional[List[str]] = [framework] if framework else None
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
        raise HTTPException(status_code=501, detail="PDF 渲染不可用，请使用 json 或 md 格式")
    return Response(content=pdf, media_type="application/pdf")
