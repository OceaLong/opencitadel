#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from typing import List

from app.domain.external.health_checker import HealthChecker
from app.domain.models.health_status import HealthStatus


class StatusService:
    """状态服务，用于检查系统的服务状态（平台域 L0，不含模型健康）"""

    def __init__(self, checkers: List[HealthChecker]) -> None:
        self._checkers = checkers

    async def check_all(self) -> List[HealthStatus]:
        results = await asyncio.gather(
            *(checker.check() for checker in self._checkers),
            return_exceptions=True,
        )

        processed_results = []
        for res in results:
            if isinstance(res, Exception):
                processed_results.append(HealthStatus(
                    service="未知服务",
                    status="error",
                    details=f"未知检查器发生错误: {str(res)}"
                ))
            else:
                processed_results.append(res)

        return processed_results
