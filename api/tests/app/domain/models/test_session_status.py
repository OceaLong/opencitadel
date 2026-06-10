#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.session import SessionStatus


def test_session_status_includes_failed():
    assert SessionStatus.FAILED.value == "failed"
    assert SessionStatus.FAILED in SessionStatus


def test_terminal_statuses_include_failed():
    terminal = {
        SessionStatus.COMPLETED,
        SessionStatus.WAITING,
        SessionStatus.CANCELLED,
        SessionStatus.FAILED,
    }
    assert SessionStatus.FAILED in terminal
