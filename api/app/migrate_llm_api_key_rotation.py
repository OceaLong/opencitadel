#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Idempotently rotate all LLM endpoint credentials to the active key id."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.logging import setup_logging
from app.infrastructure.migrations.llm_api_key_rotation import (
    rotate_endpoint_records,
)
from app.infrastructure.models.llm_endpoint import LLMEndpointORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from app.infrastructure.storage.postgres import get_postgres
from app.runtime_role import ProcessRole, set_role
from core.config import get_settings

set_role(ProcessRole.MIGRATE)
logger = logging.getLogger(__name__)


async def rotate_llm_endpoint_api_keys() -> dict[str, int]:
    settings = get_settings()
    cipher = ApiKeyCipher(
        settings.api_key_secret,
        key_id=settings.api_key_secret_id,
        previous_secrets=settings.api_key_previous_secrets,
    )
    postgres = get_postgres()
    await postgres.init()
    try:
        async with postgres.session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("rotate-llm-api-keys"),
            )
            result = await session.execute(
                select(LLMEndpointORM).order_by(LLMEndpointORM.created_at)
            )
            summary = rotate_endpoint_records(result.scalars().all(), cipher)
            await session.commit()
    finally:
        await postgres.shutdown()
    logger.info(
        "LLM credential rotation complete key_id=%s rotated=%s unchanged=%s empty=%s",
        settings.api_key_secret_id,
        summary["rotated"],
        summary["unchanged"],
        summary["empty"],
    )
    return summary


def main() -> None:
    setup_logging()
    summary = asyncio.run(rotate_llm_endpoint_api_keys())
    print(
        "LLM credential rotation complete: "
        f"rotated={summary['rotated']} "
        f"unchanged={summary['unchanged']} empty={summary['empty']}"
    )


if __name__ == "__main__":
    main()
