#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Non-leaking inspection helpers for llm_endpoints.api_key storage formats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.llm_endpoint import LLMEndpointORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


@dataclass(frozen=True)
class LLMApiKeyInspectionReport:
    total_endpoints: int
    empty_key_count: int
    legacy_plaintext_count: int
    fernet_v1_count: int
    unknown_encryption_count: int
    suspected_fernet_shape_count: int
    suspected_plaintext_count: int

    def as_log_lines(self) -> list[str]:
        return [
            f"llm_endpoints total={self.total_endpoints}",
            f"empty_api_key={self.empty_key_count}",
            f"legacy_plaintext={self.legacy_plaintext_count}",
            f"fernet_v1={self.fernet_v1_count}",
            f"unknown_encryption={self.unknown_encryption_count}",
            f"suspected_fernet_shape={self.suspected_fernet_shape_count}",
            f"suspected_plaintext={self.suspected_plaintext_count}",
        ]


def build_inspection_report(rows: Iterable[tuple[str, str | None]]) -> LLMApiKeyInspectionReport:
    total = 0
    empty_key = 0
    legacy_plaintext = 0
    fernet_v1 = 0
    unknown_encryption = 0
    suspected_fernet_shape = 0
    suspected_plaintext = 0

    for stored, encryption in rows:
        total += 1
        if not stored:
            empty_key += 1
            continue

        if encryption == ApiKeyEncryption.LEGACY_PLAINTEXT:
            legacy_plaintext += 1
            continue
        if encryption == ApiKeyEncryption.FERNET_V1:
            fernet_v1 += 1
            continue

        unknown_encryption += 1
        if ApiKeyCipher.looks_like_fernet_token(stored):
            suspected_fernet_shape += 1
        else:
            suspected_plaintext += 1

    return LLMApiKeyInspectionReport(
        total_endpoints=total,
        empty_key_count=empty_key,
        legacy_plaintext_count=legacy_plaintext,
        fernet_v1_count=fernet_v1,
        unknown_encryption_count=unknown_encryption,
        suspected_fernet_shape_count=suspected_fernet_shape,
        suspected_plaintext_count=suspected_plaintext,
    )


async def inspect_llm_api_keys(session: AsyncSession) -> LLMApiKeyInspectionReport:
    """Inspect llm_endpoints key storage without logging or returning secret values."""
    stmt = select(LLMEndpointORM.api_key, LLMEndpointORM.api_key_encryption)
    result = await session.execute(stmt)
    return build_inspection_report(result.all())


async def count_legacy_plaintext_endpoints(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(LLMEndpointORM)
        .where(
            LLMEndpointORM.api_key_encryption == ApiKeyEncryption.LEGACY_PLAINTEXT,
            LLMEndpointORM.api_key != "",
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar() or 0)
