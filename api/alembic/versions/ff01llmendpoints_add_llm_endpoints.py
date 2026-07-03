#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""add llm_endpoints table and migrate llm_models to endpoint_id FK

Revision ID: ff01llmendpoints
Revises: ee04skillprompts
Create Date: 2026-07-04

"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.infrastructure.migrations.llm_endpoint_backfill import (
    endpoint_display_name,
    group_model_rows,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from core.config import get_settings

revision: str = "ff01llmendpoints"
down_revision: Union[str, None] = "ee04skillprompts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_endpoints",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("provider", sa.String(64), nullable=False, server_default=sa.text("'openai'")),
        sa.Column("base_url", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "api_key_encryption",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'legacy_plaintext'"),
        ),
        sa.Column("owner_user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("visibility", sa.String(32), nullable=False, server_default=sa.text("'global'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )

    op.add_column("llm_models", sa.Column("endpoint_id", sa.String(255), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT id, provider, base_url, api_key, api_key_encryption,
                   owner_user_id, visibility, created_at
            FROM llm_models
            ORDER BY created_at
            """
        )
    ).fetchall()

    cipher = ApiKeyCipher(get_settings().api_key_secret)
    grouped = group_model_rows(rows, cipher=cipher)
    now = datetime.now()

    endpoint_groups: dict[tuple, str] = {}
    for group_key, group in grouped.items():
        endpoint_id = str(uuid.uuid4())
        endpoint_groups[group_key] = endpoint_id
        conn.execute(
            sa.text(
                """
                INSERT INTO llm_endpoints (
                    id, display_name, provider, base_url, api_key, api_key_encryption,
                    owner_user_id, visibility, created_at, updated_at
                ) VALUES (
                    :id, :display_name, :provider, :base_url, :api_key, :encryption,
                    :owner_user_id, :visibility, :created_at, :updated_at
                )
                """
            ),
            {
                "id": endpoint_id,
                "display_name": endpoint_display_name(group["provider"], group["base_url"]),
                "provider": group["provider"],
                "base_url": group["base_url"],
                "api_key": group["api_key"],
                "encryption": group["encryption"],
                "owner_user_id": group["owner_user_id"],
                "visibility": group["visibility"],
                "created_at": group["created_at"] or now,
                "updated_at": group["created_at"] or now,
            },
        )
        for model_id in group["model_ids"]:
            conn.execute(
                sa.text("UPDATE llm_models SET endpoint_id = :endpoint_id WHERE id = :model_id"),
                {"endpoint_id": endpoint_id, "model_id": model_id},
            )

    op.alter_column("llm_models", "endpoint_id", nullable=False)
    op.create_foreign_key(
        "fk_llm_models_endpoint_id",
        "llm_models",
        "llm_endpoints",
        ["endpoint_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_column("llm_models", "api_key_encryption")
    op.drop_column("llm_models", "api_key")
    op.drop_column("llm_models", "base_url")
    op.drop_column("llm_models", "provider")


def downgrade() -> None:
    op.add_column("llm_models", sa.Column("provider", sa.String(64), nullable=True))
    op.add_column("llm_models", sa.Column("base_url", sa.Text(), nullable=True))
    op.add_column("llm_models", sa.Column("api_key", sa.Text(), nullable=True))
    op.add_column(
        "llm_models",
        sa.Column(
            "api_key_encryption",
            sa.String(32),
            nullable=True,
            server_default=sa.text("'legacy_plaintext'"),
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT m.id, e.provider, e.base_url, e.api_key, e.api_key_encryption
            FROM llm_models m
            JOIN llm_endpoints e ON m.endpoint_id = e.id
            """
        )
    ).fetchall()
    for model_id, provider, base_url, api_key, encryption in rows:
        conn.execute(
            sa.text(
                """
                UPDATE llm_models
                SET provider = :provider,
                    base_url = :base_url,
                    api_key = :api_key,
                    api_key_encryption = :encryption
                WHERE id = :model_id
                """
            ),
            {
                "provider": provider,
                "base_url": base_url,
                "api_key": api_key,
                "encryption": encryption,
                "model_id": model_id,
            },
        )

    op.alter_column("llm_models", "provider", nullable=False, server_default=sa.text("'openai'"))
    op.alter_column("llm_models", "base_url", nullable=False, server_default=sa.text("''"))
    op.alter_column("llm_models", "api_key", nullable=False, server_default=sa.text("''"))
    op.alter_column("llm_models", "api_key_encryption", nullable=False)

    op.drop_constraint("fk_llm_models_endpoint_id", "llm_models", type_="foreignkey")
    op.drop_column("llm_models", "endpoint_id")
    op.drop_table("llm_endpoints")
