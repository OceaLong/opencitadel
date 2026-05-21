#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.interfaces.errors.exceptions import BadRequestException
from app.services.file import FileService


def test_normalize_filepath_relative_to_home():
    assert FileService._normalize_filepath("report.md") == "/home/ubuntu/report.md"


def test_normalize_filepath_keeps_absolute_path():
    assert FileService._normalize_filepath("/tmp/demo.txt") == "/tmp/demo.txt"


def test_normalize_filepath_rejects_empty():
    with pytest.raises(BadRequestException):
        FileService._normalize_filepath("   ")
