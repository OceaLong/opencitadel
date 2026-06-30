#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structured logging with request/session/task correlation."""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
task_id_var: ContextVar[Optional[str]] = ContextVar("task_id", default=None)
worker_id_var: ContextVar[Optional[str]] = ContextVar("worker_id", default=None)
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class CorrelationContextFilter(logging.Filter):
    """Inject correlation fields from ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = session_id_var.get() or "-"
        record.task_id = task_id_var.get() or "-"
        record.worker_id = worker_id_var.get() or "-"
        record.request_id = request_id_var.get() or "-"
        return True


# Backward-compatible alias
SessionContextFilter = CorrelationContextFilter


class JsonLogFormatter(logging.Formatter):
    """JSON log formatter for log aggregation systems."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": getattr(record, "session_id", "-"),
            "task_id": getattr(record, "task_id", "-"),
            "worker_id": getattr(record, "worker_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _text_formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s %(levelname)s "
        "[session=%(session_id)s task=%(task_id)s worker=%(worker_id)s request=%(request_id)s] "
        "%(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def set_request_id(request_id: Optional[str]) -> None:
    request_id_var.set(request_id)


def set_session_context(session_id: Optional[str]) -> None:
    session_id_var.set(session_id)


@contextmanager
def bind_context(
        *,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        request_id: Optional[str] = None,
) -> Iterator[None]:
    """Bind correlation fields for the current async context; reset on exit."""
    tokens: list[tuple[ContextVar[Optional[str]], Token]] = []
    try:
        if session_id is not None:
            tokens.append((session_id_var, session_id_var.set(session_id)))
        if task_id is not None:
            tokens.append((task_id_var, task_id_var.set(task_id)))
        if worker_id is not None:
            tokens.append((worker_id_var, worker_id_var.set(worker_id)))
        if request_id is not None:
            tokens.append((request_id_var, request_id_var.set(request_id)))
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def configure_structured_logging() -> None:
    """Install correlation filter and formatter on all root handlers."""
    from core.config import get_settings

    settings = get_settings()
    use_json = (settings.log_format or "text").lower() == "json"
    formatter: logging.Formatter = JsonLogFormatter() if use_json else _text_formatter()

    root = logging.getLogger()
    for handler in root.handlers:
        handler.filters = [
            f for f in handler.filters if not isinstance(f, CorrelationContextFilter)
        ]
        handler.addFilter(CorrelationContextFilter())
        handler.setFormatter(formatter)
