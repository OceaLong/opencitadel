import asyncio

from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.external.health_checker import HealthChecker
from app.domain.models.health_status import HealthStatus


class StatusService:
    """状态服务，用于检查系统的服务状态（平台域 L0，不含模型健康）"""

    def __init__(
        self,
        checkers: list[HealthChecker],
        policy_reader: PolicyHeadReader | None = None,
    ) -> None:
        self._checkers = checkers
        self._policy_reader = policy_reader

    async def check_all(self) -> list[HealthStatus]:
        results = await asyncio.gather(
            *(checker.check() for checker in self._checkers),
            return_exceptions=True,
        )

        processed_results = []
        for res in results:
            if isinstance(res, Exception):
                processed_results.append(
                    HealthStatus(
                        service="未知服务", status="error", details=f"未知检查器发生错误: {res!s}"
                    )
                )
            else:
                processed_results.append(res)

        if self._policy_reader is not None:
            readiness = self._policy_reader.readiness()
            processed_results.append(
                HealthStatus(
                    service="runtime-policy",
                    status="ok" if readiness.ready else "error",
                    details=readiness.error_key or "ready",
                )
            )

        return processed_results
