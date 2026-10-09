"""Checked reads for the dedicated evidence session factory only.

The sync ORM dispatch event covers every AsyncSession convenience entry once.
It never consumes/replaces the result. Authorization/RLS remains authoritative.
"""

import time

from scripts.execution_capacity.inventory_sql import validate_preflight
from sqlalchemy import Text, cast, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.operators import custom_op
from sqlalchemy.sql.selectable import Select, TableClause

from app.infrastructure.security.db_authorization import _AUTHORIZATION_SQL

# Actual ORM models reached by event, view/playback and observer principal reads.
ORM_TABLES = frozenset(
    {
        "execution_events",
        "execution_run_projection",
        "execution_view_runs",
        "execution_view_checkpoints",
        "execution_view_observations",
        "users",
        "files",
        "knowledge_bases",
        "knowledge_documents",
        "knowledge_base_versions",
        "knowledge_base_version_documents",
        "knowledge_document_revisions",
    }
)
ORM_FUNCTIONS = frozenset({"count", "max", "min", "sum", "coalesce"})


def aggregate_statement(statement):
    if isinstance(statement, TextClause):
        from scripts.execution_capacity.observer_sql_scope import fixed_texts

        if statement.text not in fixed_texts():
            raise ValueError("unsupported private observer text read")
        query = text(
            "SELECT count(*) AS row_count, coalesce(max(octet_length(row_to_json(q)::text)),0) AS max_bytes, "
            "coalesce(sum(octet_length(row_to_json(q)::text)),0)::bigint AS total_bytes, "
            "current_setting('transaction_read_only') AS read_only, "
            "current_setting('transaction_isolation') AS isolation, txid_current_snapshot()::text AS snapshot "
            "FROM (" + statement.text + ") AS q"
        ).bindparams(*statement._bindparams.values())
    else:
        if (
            not isinstance(statement, Select)
            or statement._for_update_arg is not None
            or statement._independent_ctes
        ):
            raise ValueError("unsupported private observer select structure")
        tables = set()
        for node in visitors.iterate(statement):
            if isinstance(node, TableClause):
                if node.name not in ORM_TABLES or node.schema not in (None, "public"):
                    raise ValueError("unsupported private observer table")
                tables.add(node.name)
            if (isinstance(node, ColumnClause) and node.is_literal) or isinstance(
                getattr(node, "operator", None), custom_op
            ):
                raise ValueError("unsupported private observer literal/operator")
            if isinstance(node, TextClause) or (
                isinstance(node, FunctionElement) and node.name.lower() not in ORM_FUNCTIONS
            ):
                raise ValueError("unsupported private observer expression")
        if not tables:
            raise ValueError("private observer select has no owned table")
        subquery = statement.subquery("q")
        size = func.octet_length(cast(func.row_to_json(subquery.table_valued()), Text))
        query = select(
            func.count().label("row_count"),
            func.coalesce(func.max(size), 0).label("max_bytes"),
            cast(func.coalesce(func.sum(size), 0), __import__("sqlalchemy").BigInteger).label(
                "total_bytes"
            ),
            func.current_setting("transaction_read_only").label("read_only"),
            func.current_setting("transaction_isolation").label("isolation"),
            cast(func.txid_current_snapshot(), Text).label("snapshot"),
        ).select_from(subquery)
    # Marks internal aggregate for instrumentation only; never bypasses guard.
    return query.execution_options(c2c_preflight=True)


class ObserverSession(Session):
    def __init__(self, *args, budget, evidence=None, **kwargs):
        super().__init__(*args, **kwargs)
        from app.infrastructure.execution.original_evidence import _EVIDENCE_KEY

        if evidence is None:
            from scripts.execution_capacity.evidence_owner import EvidenceOwner

            evidence = EvidenceOwner(budget=budget)
        self.evidence = self._original_evidence_owner = evidence
        if _EVIDENCE_KEY in self.info:
            raise ValueError("private evidence owner must be injected by session")
        self.info[_EVIDENCE_KEY] = evidence
        self.evidence_budget = budget.child(
            bytes_limit=16 * 1024 * 1024, rows_limit=16384, row_limit=1024 * 1024
        )
        evidence.reserve_state(1)
        self.evidence_uow = evidence.session_count
        evidence.session_count += 1
        # The complete SQL order is the journal's `sql` sequence. The session
        # retains only the most recent dispatch, even across a long snapshot.
        self.evidence_dispatch_count = 0
        self._evidence_latest = None
        self._evidence_latest_result = None
        self._evidence_snapshot = None
        self._evidence_transaction = None

    def latest_sql(self, *, after=None):
        if self._evidence_latest is None or (
            after is not None and self.evidence_dispatch_count != after + 1
        ):
            raise ValueError("original SQL dispatch missing or ambiguous")
        token, record = self._evidence_latest
        if not record.get("dispatched") or "error" in record:
            raise ValueError("original SQL dispatch did not complete")
        return token, record

    def sql_for_result(self, result):
        bound = self._evidence_latest_result
        if (
            bound is None
            or bound[0] is not result
            or self._evidence_latest is None
            or bound[1] is not self._evidence_latest[0]
            or bound[2] is not self._evidence_latest[1]
            or bound[3] != self.evidence_dispatch_count
        ):
            raise ValueError("original SQL result missing or superseded")
        token, record = self.latest_sql()
        if bound[1] is not token or bound[2] is not record:
            raise ValueError("original SQL result identity differs")
        return token, record

    def forget_result(self, result):
        if self._evidence_latest_result is not None and self._evidence_latest_result[0] is result:
            self._evidence_latest_result = None


@event.listens_for(ObserverSession, "do_orm_execute", retval=True)
def checked_dispatch(state):
    session = state.session
    from app.infrastructure.execution.original_evidence import session_evidence

    session_evidence(session)
    if state.statement is _AUTHORIZATION_SQL:
        return state.invoke_statement()
    query = aggregate_statement(state.statement)
    params = state.parameters or {}
    if not isinstance(params, dict):
        raise TypeError("observer executemany read unsupported")
    session.evidence_budget.charge(params)
    record = {
        "uow": session.evidence_uow,
        "parameters": session.evidence._copy(params),
        "start_ns": time.monotonic_ns(),
    }
    session.evidence.reserve_state(1)
    token = session.evidence.begin_sql(record)
    session.evidence_dispatch_count += 1
    session._evidence_latest = (token, record)
    session._evidence_latest_result = None
    try:
        connection = session.connection()
        preflight = dict(connection.execute(query, params).mappings().one())
        validate_preflight(preflight, session.evidence_budget)
        transaction = session.get_transaction()
        if (
            transaction is session._evidence_transaction
            and preflight["snapshot"] != session._evidence_snapshot
        ):
            raise ValueError("observer transaction snapshot changed")
        session._evidence_transaction = transaction
        session._evidence_snapshot = preflight["snapshot"]
        record["preflight"] = preflight
        # Pre-reserve transfer, typed hydration and retained operand copies.
        session.evidence_budget.reserve(
            preflight["total_bytes"] * 64 + preflight["row_count"] * 4096,
            preflight["total_bytes"] + preflight["row_count"] * 16,
        )
        compiled = state.statement.compile()
        session.evidence_budget.charge(compiled.params)
        record["bound_parameters"] = session.evidence._copy(compiled.params)
        record["statement"] = str(compiled)
        record["snapshot"] = preflight["snapshot"]
        result = state.invoke_statement()
        record["dispatched"] = True
        session._evidence_latest_result = (
            result,
            token,
            record,
            session.evidence_dispatch_count,
        )
        return result
    except BaseException as error:
        record["error"] = type(error).__name__
        raise
    finally:
        record["end_ns"] = time.monotonic_ns()
        session.evidence.complete_sql(token, record)
