#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from enum import Enum
from typing import Dict, Optional, List, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ConfigDict, model_validator


class ServerConfig(BaseModel):
    """HTTP 服务行为配置"""
    cors_origins: str = "*"
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    sessions_stream_interval_seconds: int = 15
    marketplace_max_upload_bytes: int = 25 * 1024 * 1024


class EmbeddingConfig(BaseModel):
    """向量嵌入配置（api_key 由环境变量 EMBEDDING_API_KEY 提供）"""
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"


class MemoryConfig(BaseModel):
    """记忆与上下文压缩配置"""
    recall_limit: int = 20
    auto_extract_enabled: bool = True
    vector_enabled: bool = False
    compact_strategy: str = "hybrid"  # rule | llm | hybrid
    compact_token_threshold: int = 32000
    compact_keep_recent: int = 12
    compact_tool_content_max_chars: int = 8000
    compact_always_on_step_boundary: bool = True
    compact_rule_trigger_threshold: int = 16000
    tool_output_offload_enabled: bool = False
    tool_output_offload_threshold_chars: int = 4000
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)


class SandboxConfig(BaseModel):
    """沙箱容器与连接池配置"""
    driver: str = "auto"  # auto | docker | kubernetes
    address: Optional[str] = None
    image: Optional[str] = None
    name_prefix: Optional[str] = None
    ttl_minutes: Optional[int] = 60
    network: Optional[str] = None
    chrome_args: Optional[str] = ""
    https_proxy: Optional[str] = None
    http_proxy: Optional[str] = None
    no_proxy: Optional[str] = None
    cleanup_interval_seconds: int = 300
    memory_limit: Optional[str] = "2g"
    cpu_limit: Optional[float] = 2.0
    pids_limit: Optional[int] = 512
    pool_enabled: bool = True
    pool_size: int = 2
    idle_timeout_minutes: int = 30
    warmup_retry_interval_seconds: float = 0.5
    k8s_namespace: str = "default"
    k8s_pod_label: str = "app=opencitadel-sandbox"


class WorkerConfig(BaseModel):
    """Agent Worker 行为配置"""
    max_concurrent_tasks: int = 4
    task_dispatch_max_retries: int = 3
    tool_timeout_seconds: int = 120
    mcp_connect_timeout_seconds: int = 30
    max_sandboxes_per_node: int = 4
    max_dynamic_sandboxes_global: int = 0
    admission_min_host_available_mb: int = 3072
    admission_reclaim_target_mb: int = 4096
    admission_poll_interval_seconds: float = 2.0
    admission_settle_seconds: float = 8.0
    admission_reclaim_enabled: bool = True
    task_execution_lease_seconds: int = 60
    reclaim_leader_lease_seconds: int = 15
    memory_probe_source: str = "auto"  # auto | host


class StreamsConfig(BaseModel):
    """Redis Stream 长度限制"""
    dispatch_maxlen: int = 10000
    task_input_maxlen: int = 10000
    task_output_maxlen: int = 50000
    stream_maxlen: int = 10000


class ObservabilityConfig(BaseModel):
    """可观测性开关（密钥由环境变量提供）"""
    otel_enabled: bool = False
    otel_service_name: str = "opencitadel-api"
    otel_exporter_endpoint: str = ""
    langfuse_enabled: bool = False  # debug-only placeholder; no Langfuse SDK integration


class AgentConfig(BaseModel):
    """Agent通用配置"""
    max_iterations: int = Field(default=100, gt=0, lt=1000)  # Agent最大迭代次数
    max_retries: int = Field(default=3, gt=1, lt=10)  # 最大重试次数
    max_search_results: int = Field(default=10, gt=1, lt=30)  # 最大搜索结果条数
    max_flow_steps: int = Field(default=50, gt=0, lt=500)  # Flow 级步骤/状态转换上限
    tool_result_max_chars: int = Field(default=32000, gt=0, lt=200_000)  # 单次工具结果回填上限
    max_run_seconds: int = Field(default=3600, gt=0, lt=86400)  # 单次 Agent 运行全局超时（秒）
    subagent_max_iterations: int = Field(default=15, gt=0, lt=500)
    subagent_max_concurrency: int = Field(default=3, gt=0, le=20)


class MCPTransport(str, Enum):
    """MCP传输类型枚举"""
    STDIO = "stdio"  # 本地输入输出
    SSE = "sse"  # 流式事件
    STREAMABLE_HTTP = "streamable_http"  # 流式HTTP


class MCPServerConfig(BaseModel):
    """MCP服务配置"""
    # 通用配置字段
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP  # 传输协议
    enabled: bool = True  # 是否开启，默认为True
    description: Optional[str] = None  # 服务器描述
    env: Optional[Dict[str, Any]] = None  # 环境变量配置

    # stdio配置
    command: Optional[str] = None  # 启用命令
    args: Optional[List[str]] = None  # 命令参数

    # streamable_http&sse配置
    url: Optional[str] = None  # MCP服务URL地址
    headers: Optional[Dict[str, Any]] = None  # MCP服务请求头

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_mcp_server_config(self):
        """校验mcp_server_config的相关信息，包含url+command"""
        # 1.判断transport是否为sse/streamable_http
        if self.transport in [MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP]:
            # 2.这两种模式需要传递url
            if not self.url:
                raise ValueError("在sse或streamable_http模式下必须传递url")
            parsed = urlparse(self.url.strip())
            if parsed.scheme not in ("http", "https"):
                raise ValueError("MCP URL 必须以 http:// 或 https:// 开头")

        # 3.判断transport是否为stdio类型
        if self.transport == MCPTransport.STDIO:
            # 4.stdio类型必须传递command
            if not self.command:
                raise ValueError("在stdio模式下必须传递command")

        return self


class MCPConfig(BaseModel):
    """应用MCP配置"""
    mcpServers: Dict[str, MCPServerConfig] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class A2AServerConfig(BaseModel):
    """A2A服务配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 唯一标识
    base_url: str  # 服务基础URL
    enabled: bool = True  # 服务是否开启


class A2AConfig(BaseModel):
    """A2A配置"""
    a2a_servers: List[A2AServerConfig] = Field(default_factory=list)


class ModelResilienceConfig(BaseModel):
    """LLM resilience: breaker, retry budget, fallback, DLQ replay."""
    enabled: bool = True
    fallback_enabled: bool = False
    allow_cross_provider_fallback: bool = False
    max_attempts_per_call: int = Field(default=3, gt=0, le=10)
    max_call_budget_seconds: float = Field(default=120.0, gt=0, le=600)
    breaker_window_seconds: int = Field(default=60, gt=0, le=3600)
    breaker_error_threshold: int = Field(default=5, gt=0, le=100)
    breaker_open_ttl_seconds: int = Field(default=60, gt=0, le=3600)
    breaker_halfopen_probe_timeout_seconds: int = Field(default=10, gt=0, le=60)
    fast_fail_on_open_circuit: bool = True
    dlq_replay_enabled: bool = False
    dlq_replay_batch_size: int = Field(default=4, gt=0, le=20)
    dlq_replay_interval_seconds: int = Field(default=5, gt=0, le=60)


class SandboxRuntimeConfig(BaseModel):
    """Sandbox environment facts injected into agent system prompts."""
    os: str = "Ubuntu 22.04 (linux/amd64)"
    user: str = "ubuntu"
    home: str = "/home/ubuntu"
    python_version: str = "3.10"
    node_version: str = "24.x"
    extra_tools: str = "bc"


class PromptConfig(BaseModel):
    """Global prompt behavior toggles."""
    writing_style: Literal["prose", "adaptive"] = "prose"


class FeatureFlagsConfig(BaseModel):
    """Static feature gates — route visibility, not runtime health."""
    enable_agent_features: bool = True
    enable_marketplace_llm_apps: bool = True
    enable_embeddings: bool = True
    enable_parallel_step_execution: bool = False
    enable_artifacts: bool = True
    enable_hitl_gates: bool = True
    enable_skill_auto_recommend: bool = False


class GateProfileSettings(BaseModel):
    """Per-profile HITL behavior for Web Operator sessions."""
    first_visit_domain_gate: bool = True
    tool_gate_call_level_enabled: bool = False
    selective_critical_only: bool = False


class HitlConfig(BaseModel):
    """Human-in-the-loop gate configuration."""
    plan_gate_enabled: bool = True
    tool_gate_task_level_enabled: bool = True
    tool_gate_call_level_enabled: bool = False
    tool_gate_risk_list: List[str] = Field(default_factory=lambda: [
        "write_file", "replace_in_file", "shell_execute", "mcp_*", "a2a",
        "browser_click", "browser_input", "browser_select_option",
        "browser_press_key", "browser_console_exec",
    ])
    critical_action_patterns: List[str] = Field(default_factory=lambda: [
        "删除", "关闭", "关单", "退款", "delete", "close", "refund", "remove",
        "confirm close", "confirm refund", "btn-confirm-close", "btn-confirm-refund",
    ])
    gate_profiles: Dict[str, GateProfileSettings] = Field(default_factory=lambda: {
        "loose": GateProfileSettings(
            first_visit_domain_gate=True,
            tool_gate_call_level_enabled=False,
            selective_critical_only=False,
        ),
        "standard": GateProfileSettings(
            first_visit_domain_gate=True,
            tool_gate_call_level_enabled=True,
            selective_critical_only=True,
        ),
        "strict": GateProfileSettings(
            first_visit_domain_gate=True,
            tool_gate_call_level_enabled=True,
            selective_critical_only=False,
        ),
    })
    takeover_timeout_minutes: int = 30


class SchedulerConfig(BaseModel):
    """Scheduled job runner configuration."""
    enabled: bool = True
    poll_interval_seconds: float = 10.0
    max_concurrent_jobs: int = 5
    max_concurrent_jobs_per_job: int = 1
    leader_lease_seconds: int = 30
    webhook_idempotency_ttl_seconds: int = 600


class KBChunkConfig(BaseModel):
    parent_max_chars: int = Field(default=2000, gt=100, le=20000)
    child_max_chars: int = Field(default=400, gt=50, le=5000)
    overlap: int = Field(default=50, ge=0, le=1000)


class KBRetrievalConfig(BaseModel):
    vector_top_k: int = Field(default=20, gt=0, le=100)
    bm25_top_k: int = Field(default=20, gt=0, le=100)
    rrf_k: int = Field(default=60, gt=0, le=1000)
    final_top_k: int = Field(default=8, gt=0, le=30)


class KBRerankConfig(BaseModel):
    enabled: bool = True
    provider: str = "llm"  # llm | api
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    model: Optional[str] = None
    timeout_seconds: float = Field(default=30.0, gt=0, le=180)


class KBGraphRAGConfig(BaseModel):
    enabled: bool = True
    max_parent_chunks_per_doc: int = Field(default=200, ge=0, le=5000)
    concurrency: int = Field(default=3, gt=0, le=20)


class KBOCRConfig(BaseModel):
    mode: str = "vision_llm"  # vision_llm | rapidocr | off
    max_pages: int = Field(default=50, ge=0, le=1000)


class KBDocumentConfig(BaseModel):
    max_bytes: int = Field(default=50 * 1024 * 1024, gt=0, le=500 * 1024 * 1024)
    max_pages: int = Field(default=1000, gt=0, le=10000)


class KBConnectorsConfig(BaseModel):
    confluence_base_url: Optional[str] = None
    feishu_base_url: Optional[str] = None
    url_allowlist: List[str] = Field(default_factory=list)
    url_denylist: List[str] = Field(default_factory=list)


class KnowledgeBaseConfig(BaseModel):
    vector_enabled: bool = True
    chunk: KBChunkConfig = Field(default_factory=KBChunkConfig)
    retrieval: KBRetrievalConfig = Field(default_factory=KBRetrievalConfig)
    rerank: KBRerankConfig = Field(default_factory=KBRerankConfig)
    graphrag: KBGraphRAGConfig = Field(default_factory=KBGraphRAGConfig)
    ocr: KBOCRConfig = Field(default_factory=KBOCRConfig)
    document: KBDocumentConfig = Field(default_factory=KBDocumentConfig)
    connectors: KBConnectorsConfig = Field(default_factory=KBConnectorsConfig)


class AppConfig(BaseModel):
    """应用运行时配置（config.yaml）"""
    server: ServerConfig = Field(default_factory=ServerConfig)
    agent_config: AgentConfig = Field(default_factory=AgentConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    sandbox_runtime: SandboxRuntimeConfig = Field(default_factory=SandboxRuntimeConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    streams: StreamsConfig = Field(default_factory=StreamsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    mcp_config: MCPConfig = Field(default_factory=MCPConfig)
    a2a_config: A2AConfig = Field(default_factory=A2AConfig)
    model_resilience: ModelResilienceConfig = Field(default_factory=ModelResilienceConfig)
    feature_flags: FeatureFlagsConfig = Field(default_factory=FeatureFlagsConfig)
    hitl: HitlConfig = Field(default_factory=HitlConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)

    model_config = ConfigDict(extra="allow")
