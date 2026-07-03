#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone database migration entrypoint (run once per deploy)."""
from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config

from app.infrastructure.logging import setup_logging
from app.migrate_llm_api_keys import migrate_legacy_plaintext_api_keys
from app.migrate_app_config_seed import seed_app_config_from_yaml_if_empty
from app.migrate_mcp_a2a_from_blob import migrate_mcp_a2a_from_blob
from app.runtime_role import ProcessRole, set_role

set_role(ProcessRole.MIGRATE)


async def run_data_migrations() -> None:
    migrated = await migrate_legacy_plaintext_api_keys()
    print(f"LLM API key migration complete: migrated={migrated}")

    seeded = await seed_app_config_from_yaml_if_empty()
    print(f"AppConfig YAML seed complete: seeded={seeded}")

    migrated_integrations = await migrate_mcp_a2a_from_blob()
    print(f"MCP/A2A blob migration complete: {migrated_integrations}")


def main() -> None:
    setup_logging()
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    print("Database schema migrations applied successfully.")

    asyncio.run(run_data_migrations())


if __name__ == "__main__":
    main()
