from types import SimpleNamespace

import pytest

from app.application.services.status_service import StatusService


class _PolicyReader:
    def __init__(self, *, ready: bool, error_key: str | None = None) -> None:
        self._readiness = SimpleNamespace(ready=ready, error_key=error_key)

    def readiness(self):
        return self._readiness


@pytest.mark.asyncio
async def test_status_reports_runtime_policy_degradation() -> None:
    service = StatusService(
        checkers=[],
        policy_reader=_PolicyReader(
            ready=False,
            error_key="runtimePolicy.integrity",
        ),
    )

    statuses = await service.check_all()

    assert len(statuses) == 1
    assert statuses[0].service == "runtime-policy"
    assert statuses[0].status == "error"
    assert statuses[0].details == "runtimePolicy.integrity"
