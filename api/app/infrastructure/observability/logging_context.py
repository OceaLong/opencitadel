#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structured logging with session correlation."""
import logging
from contextvars import ContextVar
from typing import Optional

session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)


class SessionContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = session_id_var.get() or "-"
        return True


def set_session_context(session_id: Optional[str]) -> None:
    session_id_var.set(session_id)


def configure_structured_logging() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, SessionContextFilter) for f in handler.filters):
            handler.addFilter(SessionContextFilter())
            formatter = logging.Formatter(
                "%(asctime)s %(levelname)s [session=%(session_id)s] %(name)s: %(message)s"
            )
            handler.setFormatter(formatter)
