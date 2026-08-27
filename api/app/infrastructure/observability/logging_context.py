"""Structured logging with request/session/task correlation."""

import json
import logging

from app.application.request_context import (
    request_id_var,
    session_id_var,
    task_id_var,
    worker_id_var,
)


class CorrelationContextFilter(logging.Filter):
    """Inject correlation fields from ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = session_id_var.get() or "-"
        record.task_id = task_id_var.get() or "-"
        record.worker_id = worker_id_var.get() or "-"
        record.request_id = request_id_var.get() or "-"
        return True


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


def configure_structured_logging(*, log_format: str) -> None:
    """Install correlation filter and formatter on all root handlers."""
    use_json = (log_format or "text").lower() == "json"
    formatter: logging.Formatter = JsonLogFormatter() if use_json else _text_formatter()

    root = logging.getLogger()
    for handler in root.handlers:
        handler.filters = [
            f for f in handler.filters if not isinstance(f, CorrelationContextFilter)
        ]
        handler.addFilter(CorrelationContextFilter())
        handler.setFormatter(formatter)
