from unittest.mock import AsyncMock

import pytest

from app.application.ports.execution_analysis import (
    AnalysisCapture,
    AuthorityState,
    CoverageChanged,
)
from app.application.services.execution_analysis_service import ExecutionAnalysisService
from app.domain.models.scope import OwnerScope, Principal


@pytest.mark.asyncio
async def test_run_page_rechecks_authority_after_fixed_page_read():
    capture = AnalysisCapture("fixed", AuthorityState(1, "m"), {})
    repository = AsyncMock()
    repository.capture.return_value = capture
    repository.current.side_effect = [capture.authority, AuthorityState(2, "revoked")]
    repository.run_page.return_value = {"items": [{"run_id": "secret"}]}
    service = ExecutionAnalysisService(repository, Principal(user_id="u"))
    with pytest.raises(CoverageChanged):
        await service.runs(
            OwnerScope.personal("u"),
            {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
            "day",
            "UTC",
            "fixed",
        )


@pytest.mark.asyncio
async def test_run_page_requires_fixed_watermark_and_bounded_limit_before_io():
    repository = AsyncMock()
    service = ExecutionAnalysisService(repository, Principal(user_id="u"))
    for watermark, limit in [(None, 50), ("fixed", 201), ("fixed", 0)]:
        with pytest.raises(ValueError, match="invalid_analysis_page"):
            await service.runs(OwnerScope.personal("u"), {}, "day", "UTC", watermark, limit=limit)
    repository.capture.assert_not_awaited()
