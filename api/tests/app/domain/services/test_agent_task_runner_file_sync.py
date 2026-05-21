#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.services.agent_task_runner import FILE_MUTATING_FUNCTIONS


@pytest.mark.parametrize(
    ("function_name", "should_sync"),
    [
        ("write_file", True),
        ("replace_in_file", True),
        ("read_file", False),
        ("search_in_file", False),
        ("find_files", False),
    ],
)
def test_file_mutating_functions(function_name, should_sync):
    assert (function_name in FILE_MUTATING_FUNCTIONS) is should_sync
