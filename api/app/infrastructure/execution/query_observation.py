"""Opt-in private capture of named repository SQL at the actual driver boundary.

No query is executed by this module. Capacity engines install listeners once before
requests and remove them only after drain; capture entry/exit never mutates events.
Capacity callers retain this object privately: SQL/parameters may contain secrets.
"""

from asyncio import current_task
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic_ns
from weakref import WeakKeyDictionary

from sqlalchemy import event

QUERY_NAMES = frozenset(
    {
        "steps.count",
        "steps.page",
        "analysis.capture",
        "analysis.page",
        "analysis.charts",
        "analysis.points",
        "analysis.scores",
        "matrix.results",
    }
)
_ACTIVE = ContextVar("execution_repository_capture", default=None)
_INSTALLED = WeakKeyDictionary()


@dataclass(repr=False)
class CapturedStatement:
    repository_query: str
    sql: str
    parameters: tuple
    backend_pid: int
    started_ns: int
    ended_ns: int | None = None
    connection: object = None
    metadata: object = None


@dataclass(repr=False)
class QueryCapture:
    sample_id: str
    action_id: str
    clock_id: str
    clone_id: str
    expected: tuple[str, ...]
    statements: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    closed: bool = False


def named_query(statement, name):
    if name not in QUERY_NAMES:
        raise ValueError("unknown fixed repository query")
    return statement.execution_options(execution_repository_query=name)


def _task():
    try:
        return current_task()
    except RuntimeError:  # synchronous capture outside an event loop
        return None


class QueryObservation:
    """Capacity engine event lifetime; C3 must set up and tear down quiescently.

    close() refuses active captures and leaves listeners installed so draining can
    finish before retrying. C3 must also drain all other engine users before close.
    No request is serialized. Use install_query_observation, not this constructor.
    """

    def __init__(self, engine):
        self.engine = engine
        self.active = 0
        self.closed = False
        event.listen(engine, "before_cursor_execute", self.before)
        event.listen(engine, "after_cursor_execute", self.after)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.active:
            raise ValueError("repository captures must drain before listener removal")
        if not self.closed:
            event.remove(self.engine, "before_cursor_execute", self.before)
            event.remove(self.engine, "after_cursor_execute", self.after)
            del _INSTALLED[self.engine]
            self.closed = True

    def before(self, conn, cursor, sql, parameters, context, executemany):
        state = _ACTIVE.get()
        if (
            state is None
            or state.owner is not self
            or state.task is not _task()
            or state.capture.closed
        ):
            return
        capture = state.capture
        name = context.execution_options.get("execution_repository_query")
        if name is None:
            return
        if name not in QUERY_NAMES or executemany or not isinstance(parameters, tuple):
            capture.errors.append("unsupported_repository_driver_binding")
            return
        driver = conn.connection.driver_connection
        if not hasattr(driver, "get_server_pid"):
            capture.errors.append("asyncpg_backend_identity_unavailable")
            return
        metadata = None
        if state.observer is not None:
            try:
                metadata = state.observer.before(conn, name)
            except Exception as error:  # noqa: BLE001 - preserve original query; retain observer failure
                capture.errors.append("diagnostic_preparation_" + type(error).__name__)
        item = CapturedStatement(
            name,
            sql,
            deepcopy(parameters),
            driver.get_server_pid(),
            monotonic_ns(),
            connection=conn,
            metadata=metadata,
        )
        capture.statements.append(item)
        state.pending[id(context)] = item

    def after(self, conn, cursor, sql, parameters, context, executemany):
        state = _ACTIVE.get()
        if (
            state is not None
            and state.owner is self
            and state.task is _task()
            and not state.capture.closed
            and id(context) in state.pending
        ):
            item = state.pending.pop(id(context))
            item.ended_ns = monotonic_ns()
            if state.observer is not None and item.metadata is not None:
                try:
                    state.observer.after(conn, item)
                except Exception as error:  # noqa: BLE001 - preserve original query; retain observer failure
                    state.capture.errors.append("diagnostic_observation_" + type(error).__name__)


def install_query_observation(engine):
    """Install once during quiescent setup, before any engine requests/connections."""
    if engine in _INSTALLED:
        raise ValueError("repository observation already installed")
    facility = QueryObservation(engine)
    _INSTALLED[engine] = facility
    return facility


@dataclass(repr=False)
class _CaptureState:
    owner: QueryObservation
    capture: QueryCapture
    observer: object
    task: object
    pending: dict = field(default_factory=dict)


@contextmanager
def capture_queries(engine, *, sample_id, action_id, clock_id, clone_id, expected, _observer=None):
    """Task-local capture around the authorized operation, with preregistered names.

    Requires quiescent engine installation first. Entering/exiting a capture only
    updates task-local state and the drain count; it never alters engine listeners.
    """
    if _ACTIVE.get() is not None or not expected or any(x not in QUERY_NAMES for x in expected):
        raise ValueError("invalid/nested repository capture")
    facility = _INSTALLED.get(engine)
    if facility is None or facility.closed:
        raise ValueError("repository observation must be installed before requests")
    capture = QueryCapture(sample_id, action_id, clock_id, clone_id, tuple(expected))
    state = _CaptureState(facility, capture, _observer, _task())
    token = _ACTIVE.set(state)
    facility.active += 1
    try:
        yield capture
    finally:
        _ACTIVE.reset(token)
        facility.active -= 1
        capture.closed = True
        if state.pending:
            capture.errors.append("repository_execution_incomplete")
        if tuple(x.repository_query for x in capture.statements) != capture.expected:
            capture.errors.append("repository_statement_sequence_mismatch")
