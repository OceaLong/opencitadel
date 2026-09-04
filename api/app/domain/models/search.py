from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SearchProviderAvailability(StrEnum):
    """Web 搜索能力判定（P2-11 单源）：由 search/providers.py 独家产出。"""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"


class SearchCapability(BaseModel):
    """一个 SEARCH_PROVIDER 配置的可用性判定结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    availability: SearchProviderAvailability


class SearchResultItem(BaseModel):
    """搜索结果条目数据模型"""

    url: str  # 搜索条目URL地址
    title: str  # 搜索条目标题
    snippet: str = ""  # 搜索条目简介


class SearchResults(BaseModel):
    """搜索结果数据模型"""

    query: str  # 用户的搜索词
    date_range: str | None = None  # 日期检索范围
    total_results: int = 0  # 搜索结果条数
    results: list[SearchResultItem] = Field(default_factory=list)  # 搜索结果列表
