from pydantic import BaseModel, Field


class TimeoutRequest(BaseModel):
    """激活超时销毁请求"""

    minutes: int | None = Field(default=None, description="分钟数")
