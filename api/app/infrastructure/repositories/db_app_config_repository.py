#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Database-backed app configuration (MCP/A2A/agent) replacing config.yaml secrets."""
import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.infrastructure.storage.postgres import get_postgres

logger = logging.getLogger(__name__)

_CONFIG_TABLE = "app_runtime_config"


async def ensure_config_table(session: AsyncSession) -> None:
    await session.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_CONFIG_TABLE} (
            key VARCHAR(128) PRIMARY KEY,
            value JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))


class DbAppConfigRepository:
    """Store runtime config in PostgreSQL instead of plaintext config.yaml."""

    async def get_json(self, key: str) -> Optional[dict]:
        postgres = get_postgres()
        async with postgres.session_factory() as session:
            await ensure_config_table(session)
            result = await session.execute(
                text(f"SELECT value FROM {_CONFIG_TABLE} WHERE key = :key"),
                {"key": key},
            )
            row = result.fetchone()
            await session.commit()
        return row.value if row else None

    async def save_json(self, key: str, value: dict) -> None:
        postgres = get_postgres()
        async with postgres.session_factory() as session:
            await ensure_config_table(session)
            await session.execute(
                text(f"""
                    INSERT INTO {_CONFIG_TABLE} (key, value, updated_at)
                    VALUES (:key, :value::jsonb, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                """),
                {"key": key, "value": json.dumps(value)},
            )
            await session.commit()

    async def get_agent_config(self) -> Optional[AgentConfig]:
        data = await self.get_json("agent_config")
        return AgentConfig.model_validate(data) if data else None

    async def get_mcp_config(self) -> Optional[MCPConfig]:
        data = await self.get_json("mcp_config")
        return MCPConfig.model_validate(data) if data else None

    async def get_a2a_config(self) -> Optional[A2AConfig]:
        data = await self.get_json("a2a_config")
        return A2AConfig.model_validate(data) if data else None
