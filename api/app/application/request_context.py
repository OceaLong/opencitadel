"""Framework-neutral correlation context shared across request and worker boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)
worker_id_var: ContextVar[str | None] = ContextVar("worker_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()


@contextmanager
def bind_context(
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[None]:
    """Bind correlation fields for the current async context; reset on exit."""

    tokens: list[tuple[ContextVar[str | None], Token]] = []
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
        for variable, token in reversed(tokens):
            variable.reset(token)
