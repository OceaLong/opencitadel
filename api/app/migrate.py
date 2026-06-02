#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone database migration entrypoint (run once per deploy)."""
from alembic import command
from alembic.config import Config


def main() -> None:
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    print("Database migrations applied successfully.")


if __name__ == "__main__":
    main()
