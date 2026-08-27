from enum import StrEnum

from pydantic import BaseModel, Field


class HealthCheckStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class HealthStatus(BaseModel):
    """健康检查状态"""

    service: str = Field(default="", description="健康检查对应的服务名字")
    status: HealthCheckStatus = Field(
        default=HealthCheckStatus.OK,
        description="健康检查状态",
    )
    details: str = Field(default="", description="出错时的详情提示")
