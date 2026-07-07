#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENV", "test")

from app.main import app


@pytest.fixture(scope="session")
def _db_schema() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def client(_db_schema) -> TestClient:
    with TestClient(app) as c:
        yield c
