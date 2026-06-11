#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Encrypt legacy plaintext llm_models.api_key values."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.infrastructure.logging import setup_logging
from app.infrastructure.models.llm_model import LLMModelORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption
from app.infrastructure.security.llm_key_inspector import (
    count_legacy_plaintext_models,
    inspect_llm_api_keys,
)
from app.infrastructure.storage.postgres import get_postgres
from app.runtime_role import ProcessRole, set_role
from core.config import get_settings

set_role(ProcessRole.MIGRATE)

logger = logging.getLogger(__name__)


async def migrate_legacy_plaintext_api_keys() -> int:
    settings = get_settings()
    postgres = get_postgres()
    await postgres.init()

    migrated = 0
    try:
        async with postgres.session_factory() as session:
            report = await inspect_llm_api_keys(session)
            for line in report.as_log_lines():
                logger.info("LLM API key inspection: %s", line)

            legacy_count = await count_legacy_plaintext_models(session)
            if legacy_count == 0:
                logger.info("No legacy plaintext LLM API keys to migrate")
                return 0

            cipher = ApiKeyCipher(settings.api_key_secret)
            stmt = (
                select(LLMModelORM)
                .where(
                    LLMModelORM.api_key_encryption == ApiKeyEncryption.LEGACY_PLAINTEXT,
                    LLMModelORM.api_key != "",
                )
                .order_by(LLMModelORM.created_at)
            )
            result = await session.execute(stmt)
            records = list(result.scalars().all())

            for record in records:
                encrypted = cipher.encrypt(record.api_key)
                record.api_key = encrypted
                record.api_key_encryption = ApiKeyEncryption.FERNET_V1
                migrated += 1
                logger.info("Encrypted legacy plaintext LLM API key for model_id=%s", record.id)

            await session.commit()
    finally:
        await postgres.shutdown()

    logger.info("LLM API key migration complete: migrated=%s", migrated)
    return migrated


def main() -> None:
    setup_logging()
    migrated = asyncio.run(migrate_legacy_plaintext_api_keys())
    print(f"LLM API key migration complete: migrated={migrated}")


if __name__ == "__main__":
    main()
