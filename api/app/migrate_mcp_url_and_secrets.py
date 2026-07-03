#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Encrypt legacy plaintext MCP url/headers/env values."""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.infrastructure.logging import setup_logging
from app.infrastructure.models.integration_server import MCPServerORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption
from app.infrastructure.security.secret_dict_cipher import encrypt_secret_dict, encrypt_url
from app.infrastructure.storage.postgres import get_postgres
from app.runtime_role import ProcessRole, set_role
from core.config import get_settings

set_role(ProcessRole.MIGRATE)

logger = logging.getLogger(__name__)


async def migrate_mcp_url_and_secrets() -> dict:
    settings = get_settings()
    cipher = ApiKeyCipher(settings.api_key_secret)
    postgres = get_postgres()
    await postgres.init()

    migrated_urls = 0
    migrated_headers = 0
    migrated_env = 0
    try:
        async with postgres.session_factory() as session:
            result = await session.execute(select(MCPServerORM).order_by(MCPServerORM.created_at))
            records = list(result.scalars().all())

            for record in records:
                if record.url_encryption == ApiKeyEncryption.LEGACY_PLAINTEXT and record.url:
                    enc_url, url_enc = encrypt_url(record.url, cipher)
                    if url_enc != ApiKeyEncryption.LEGACY_PLAINTEXT:
                        record.url = enc_url
                        record.url_encryption = url_enc
                        migrated_urls += 1
                        logger.info("Encrypted legacy plaintext MCP url for server_id=%s", record.id)

                if record.headers_encryption == ApiKeyEncryption.LEGACY_PLAINTEXT and record.headers:
                    enc_headers, headers_enc = encrypt_secret_dict(record.headers, cipher)
                    if headers_enc != ApiKeyEncryption.LEGACY_PLAINTEXT:
                        record.headers = enc_headers
                        record.headers_encryption = headers_enc
                        migrated_headers += 1
                        logger.info("Encrypted legacy plaintext MCP headers for server_id=%s", record.id)

                if record.env_encryption == ApiKeyEncryption.LEGACY_PLAINTEXT and record.env:
                    enc_env, env_enc = encrypt_secret_dict(record.env, cipher)
                    if env_enc != ApiKeyEncryption.LEGACY_PLAINTEXT:
                        record.env = enc_env
                        record.env_encryption = env_enc
                        migrated_env += 1
                        logger.info("Encrypted legacy plaintext MCP env for server_id=%s", record.id)

            if migrated_urls or migrated_headers or migrated_env:
                await session.commit()
            else:
                await session.rollback()

        summary = {"urls": migrated_urls, "headers": migrated_headers, "env": migrated_env}
        logger.info("MCP url/secret migration complete: %s", summary)
        return summary
    finally:
        await postgres.shutdown()


def main() -> None:
    setup_logging()
    import asyncio

    summary = asyncio.run(migrate_mcp_url_and_secrets())
    print(f"MCP url/secret migration complete: {summary}")


if __name__ == "__main__":
    main()
