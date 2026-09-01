"""Immutable policy values frozen into newly admitted Runs."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ExecutionPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentExecutionPolicy(_ExecutionPolicyModel):
    max_iterations: int = Field(default=12, ge=1, le=100)
    max_retries: int = Field(default=2, ge=0, le=10)


class ModelResiliencePolicy(_ExecutionPolicyModel):
    enabled: bool = True
    fallback_enabled: bool = False
    allow_cross_provider_fallback: bool = False
    fallback_on_quota_exceeded: bool = True
    allow_cross_provider_fallback_on_quota: bool = True
    max_attempts_per_call: int = Field(default=3, ge=1, le=10)
    max_call_budget_seconds: float = Field(default=120.0, gt=0, le=600)
    breaker_window_seconds: int = Field(default=60, ge=1, le=3_600)
    breaker_error_threshold: int = Field(default=5, ge=1, le=100)
    breaker_open_ttl_seconds: int = Field(default=60, ge=1, le=3_600)
    breaker_halfopen_probe_timeout_seconds: int = Field(default=10, ge=1, le=60)
    fast_fail_on_open_circuit: bool = True


class ActivityExecutionPolicy(_ExecutionPolicyModel):
    tool_timeout_seconds: int = Field(default=120, ge=1, le=86_400)
    mcp_connect_timeout_seconds: int = Field(default=30, ge=1, le=3_600)


class MemoryExecutionPolicy(_ExecutionPolicyModel):
    recall_limit: int = Field(default=20, ge=1, le=100)
    vector_enabled: bool = False


class KnowledgeChunkPolicy(_ExecutionPolicyModel):
    parent_max_chars: int = Field(default=2_000, gt=100, le=20_000)
    child_max_chars: int = Field(default=400, gt=50, le=5_000)
    overlap: int = Field(default=50, ge=0, le=1_000)

    @model_validator(mode="after")
    def validate_chunk_relationships(self) -> "KnowledgeChunkPolicy":
        if self.child_max_chars > self.parent_max_chars:
            raise ValueError("child_max_chars must not exceed parent_max_chars")
        if self.overlap >= self.child_max_chars:
            raise ValueError("overlap must be smaller than child_max_chars")
        return self


class KnowledgeRetrievalPolicy(_ExecutionPolicyModel):
    vector_top_k: int = Field(default=20, ge=1, le=100)
    bm25_top_k: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=1_000)
    final_top_k: int = Field(default=8, ge=1, le=30)


class KnowledgeRerankPolicy(_ExecutionPolicyModel):
    enabled: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0, le=180)


class KnowledgeGraphRagPolicy(_ExecutionPolicyModel):
    enabled: bool = True
    max_parent_chunks_per_doc: int = Field(default=200, ge=0, le=5_000)
    concurrency: int = Field(default=3, ge=1, le=20)
    max_chunks: int = Field(default=10_000, ge=1, le=1_000_000)
    max_llm_calls: int = Field(default=10_000, ge=1, le=1_000_000)
    max_tokens: int = Field(default=1_000_000, ge=1, le=1_000_000_000)
    deadline_seconds: float = Field(default=300.0, gt=0, le=3_600)


class KnowledgeOcrMode(StrEnum):
    VISION_LLM = "vision_llm"
    RAPIDOCR = "rapidocr"
    OFF = "off"


class KnowledgeOcrPolicy(_ExecutionPolicyModel):
    mode: KnowledgeOcrMode = KnowledgeOcrMode.VISION_LLM
    max_pages: int = Field(default=50, ge=0, le=1_000)


class KnowledgeDocumentPolicy(_ExecutionPolicyModel):
    max_bytes: int = Field(default=50 * 1_024 * 1_024, ge=1, le=500 * 1_024 * 1_024)
    max_pages: int = Field(default=1_000, ge=1, le=10_000)


class KnowledgeBaseExecutionPolicy(_ExecutionPolicyModel):
    vector_enabled: bool = True
    chunk: KnowledgeChunkPolicy = Field(default_factory=KnowledgeChunkPolicy)
    retrieval: KnowledgeRetrievalPolicy = Field(default_factory=KnowledgeRetrievalPolicy)
    rerank: KnowledgeRerankPolicy = Field(default_factory=KnowledgeRerankPolicy)
    graphrag: KnowledgeGraphRagPolicy = Field(default_factory=KnowledgeGraphRagPolicy)
    ocr: KnowledgeOcrPolicy = Field(default_factory=KnowledgeOcrPolicy)
    document: KnowledgeDocumentPolicy = Field(default_factory=KnowledgeDocumentPolicy)


class ExecutionPolicy(_ExecutionPolicyModel):
    agent: AgentExecutionPolicy = Field(default_factory=AgentExecutionPolicy)
    model_resilience: ModelResiliencePolicy = Field(default_factory=ModelResiliencePolicy)
    activity: ActivityExecutionPolicy = Field(default_factory=ActivityExecutionPolicy)
    memory: MemoryExecutionPolicy = Field(default_factory=MemoryExecutionPolicy)
    knowledge_base: KnowledgeBaseExecutionPolicy = Field(
        default_factory=KnowledgeBaseExecutionPolicy
    )


__all__ = [
    "ActivityExecutionPolicy",
    "AgentExecutionPolicy",
    "ExecutionPolicy",
    "KnowledgeBaseExecutionPolicy",
    "KnowledgeChunkPolicy",
    "KnowledgeDocumentPolicy",
    "KnowledgeGraphRagPolicy",
    "KnowledgeOcrMode",
    "KnowledgeOcrPolicy",
    "KnowledgeRerankPolicy",
    "KnowledgeRetrievalPolicy",
    "MemoryExecutionPolicy",
    "ModelResiliencePolicy",
]
