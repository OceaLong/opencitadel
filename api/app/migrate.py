#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone database migration entrypoint (run once per deploy)."""
from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config

from app.infrastructure.logging import setup_logging
from app.migrate_llm_api_keys import migrate_legacy_plaintext_api_keys
from app.runtime_role import ProcessRole, set_role

set_role(ProcessRole.MIGRATE)


def main() -> None:
    setup_logging()
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    print("Database schema migrations applied successfully.")

    migrated = asyncio.run(migrate_legacy_plaintext_api_keys())
    print(f"LLM API key migration complete: migrated={migrated}")


if __name__ == "__main__":
    main()
