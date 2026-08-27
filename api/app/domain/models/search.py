from pydantic import BaseModel, Field


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
