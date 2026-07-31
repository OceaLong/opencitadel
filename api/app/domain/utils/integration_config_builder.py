#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Convert integration server records to legacy AppConfig shapes."""
from typing import List

from app.domain.models.app_config import A2AConfig, A2AServerConfig, MCPConfig, MCPServerConfig
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord


def mcp_records_to_config(records: List[MCPServerRecord]) -> MCPConfig:
    servers = {}
    for record in records:
        legacy_extra = {
            key: value
            for key, value in record.extra.items()
            if key != "tool_policies"
        }
        servers[record.name] = MCPServerConfig(
            **legacy_extra,
            transport=record.transport,
            enabled=record.enabled,
            description=record.description,
            env=record.env,
            command=record.command,
            args=record.args,
            url=record.url,
            headers=record.headers,
            tool_policies=record.tool_policies,
        )
    return MCPConfig(mcpServers=servers)


def a2a_records_to_config(records: List[A2AServerRecord]) -> A2AConfig:
    return A2AConfig(
        a2a_servers=[
            A2AServerConfig(
                id=record.id,
                base_url=record.base_url,
                enabled=record.enabled,
                tool_policies=record.tool_policies,
            )
            for record in records
        ]
    )
