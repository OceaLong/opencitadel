#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENV", "test")

from app.main import app


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c
