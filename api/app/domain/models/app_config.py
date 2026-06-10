#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from enum import Enum
from typing import Dict, Optional, List, Any

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
    compact_tool_content_max_chars: int = 2000
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)


class SandboxConfig(BaseModel):
    """沙箱容器与连接池配置"""
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


class WorkerConfig(BaseModel):
    """Agent Worker 行为配置"""
    max_concurrent_tasks: int = 4
    task_dispatch_max_retries: int = 3
    tool_timeout_seconds: int = 120


class StreamsConfig(BaseModel):
    """Redis Stream 长度限制"""
    dispatch_maxlen: int = 10000
    task_input_maxlen: int = 10000
    task_output_maxlen: int = 50000
    stream_maxlen: int = 10000


class ObservabilityConfig(BaseModel):
    """可观测性开关（密钥由环境变量提供）"""
    otel_enabled: bool = False
    otel_service_name: str = "my-manus-api"
    otel_exporter_endpoint: str = ""
    langfuse_enabled: bool = False


class AgentConfig(BaseModel):
    """Agent通用配置"""
    max_iterations: int = Field(default=100, gt=0, lt=1000)  # Agent最大迭代次数
    max_retries: int = Field(default=3, gt=1, lt=10)  # 最大重试次数
    max_search_results: int = Field(default=10, gt=1, lt=30)  # 最大搜索结果条数
    max_flow_steps: int = Field(default=50, gt=0, lt=500)  # Flow 级步骤/状态转换上限
    tool_result_max_chars: int = Field(default=8000, gt=0, lt=200_000)  # 单次工具结果回填上限
    max_run_seconds: int = Field(default=3600, gt=0, lt=86400)  # 单次 Agent 运行全局超时（秒）


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


class AppConfig(BaseModel):
    """应用运行时配置（config.yaml）"""
    server: ServerConfig = Field(default_factory=ServerConfig)
    agent_config: AgentConfig = Field(default_factory=AgentConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    streams: StreamsConfig = Field(default_factory=StreamsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    mcp_config: MCPConfig = Field(default_factory=MCPConfig)
    a2a_config: A2AConfig = Field(default_factory=A2AConfig)

    model_config = ConfigDict(extra="allow")
