#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Migrate MCP/A2A entries from app_configs blob into dedicated tables."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.domain.models.app_config import AppConfig
from app.domain.models.llm_model import ResourceVisibility
from app.infrastructure.models.app_config import AppConfigModel
from app.infrastructure.models.integration_server import A2AServerORM, MCPServerORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.secret_dict_cipher import encrypt_secret_dict, encrypt_url
from app.infrastructure.storage.postgres import get_postgres
from core.config import get_settings

logger = logging.getLogger(__name__)


async def migrate_mcp_a2a_from_blob() -> dict:
    settings = get_settings()
    cipher = ApiKeyCipher(settings.api_key_secret)
    postgres = get_postgres()
    await postgres.init()
    migrated_mcp = 0
    migrated_a2a = 0
    try:
        async with postgres.session_factory() as session:
            result = await session.execute(select(AppConfigModel).where(AppConfigModel.id == "global"))
            record = result.scalar_one_or_none()
            if record is None or not record.payload:
                return {"mcp": 0, "a2a": 0}

            app_config = AppConfig.model_validate(record.payload)

            existing_mcp = await session.execute(select(MCPServerORM.id).limit(1))
            if existing_mcp.scalar_one_or_none() is None:
                for name, cfg in app_config.mcp_config.mcpServers.items():
                    enc_headers, headers_enc = encrypt_secret_dict(cfg.headers, cipher)
                    enc_env, env_enc = encrypt_secret_dict(cfg.env, cipher)
                    enc_url, url_enc = encrypt_url(cfg.url, cipher)
                    session.add(
                        MCPServerORM(
                            id=str(uuid.uuid4()),
                            name=name,
                            transport=cfg.transport.value if hasattr(cfg.transport, "value") else str(cfg.transport),
                            enabled=cfg.enabled,
                            description=cfg.description,
                            command=cfg.command,
                            args=cfg.args,
                            url=enc_url,
                            url_encryption=url_enc,
                            headers=enc_headers,
                            headers_encryption=headers_enc,
                            env=enc_env,
                            env_encryption=env_enc,
                            extra={},
                            owner_user_id=None,
                            visibility=ResourceVisibility.GLOBAL.value,
                        )
                    )
                    migrated_mcp += 1

            existing_a2a = await session.execute(select(A2AServerORM.id).limit(1))
            if existing_a2a.scalar_one_or_none() is None:
                for cfg in app_config.a2a_config.a2a_servers:
                    session.add(
                        A2AServerORM(
                            id=cfg.id or str(uuid.uuid4()),
                            base_url=cfg.base_url,
                            enabled=cfg.enabled,
                            owner_user_id=None,
                            visibility=ResourceVisibility.GLOBAL.value,
                        )
                    )
                    migrated_a2a += 1

            if migrated_mcp or migrated_a2a:
                payload = record.payload.copy()
                payload["mcp_config"] = {"mcpServers": {}}
                payload["a2a_config"] = {"a2a_servers": []}
                record.payload = payload
                await session.commit()
                logger.info("MCP/A2A blob 迁移完成: mcp=%s a2a=%s", migrated_mcp, migrated_a2a)
            else:
                await session.rollback()

        return {"mcp": migrated_mcp, "a2a": migrated_a2a}
    finally:
        await postgres.shutdown()
