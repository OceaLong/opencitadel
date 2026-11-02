"""Actual AsyncSession/ORM dispatch with private SQLite and injected PG preflight."""

from decimal import Decimal

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
from sqlalchemy import Column, Integer, MetaData, Numeric, Table, create_engine, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import registry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["execute", "scalar", "scalars", "stream", "stream_scalars", "inventory"]
)
@pytest.mark.parametrize("oversize", [False, True])
async def test_observer_public_dispatch_once_before_real_typed_result(
    monkeypatch, operation, oversize
):
    from scripts.execution_capacity.observer_session import ObserverSession

    metadata = MetaData()
    table = Table(
        "execution_run_projection",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", Numeric(20, 3)),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), {"id": 1, "value": Decimal("2.125")})
    mapper = registry()

    class Projection:
        pass

    mapper.map_imperatively(Projection, table)
    calls = []
    original = Connection.execute

    def injected(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            calls.append(("preflight", connection, parameters))
            return original(
                connection,
                select(
                    __import__("sqlalchemy").literal(1).label("row_count"),
                    __import__("sqlalchemy").literal(500 if oversize else 20).label("max_bytes"),
                    __import__("sqlalchemy").literal(500 if oversize else 20).label("total_bytes"),
                    __import__("sqlalchemy").literal("on").label("read_only"),
                    __import__("sqlalchemy").literal("repeatable read").label("isolation"),
                    __import__("sqlalchemy").literal("fixture:1").label("snapshot"),
                ),
            )
        calls.append(("typed", connection, parameters))
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", injected)

    class BoundObserver(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=EvidenceBudget(row_limit=100), **kwargs)

    statement = select(Projection).where(
        table.c.id == __import__("sqlalchemy").bindparam("identity")
    )
    async with AsyncSession(sync_session_class=BoundObserver) as session:

        async def dispatch():
            if operation == "inventory":
                from scripts.execution_capacity.inventory_sql import InventoryQueries

                return await InventoryQueries(session).rows(
                    "projection", "SELECT * FROM execution_run_projection", {"identity": 1}
                )
            return await getattr(session, operation)(statement, {"identity": 1})

        if oversize:
            with pytest.raises(EvidenceQuotaError, match="quota"):
                await dispatch()
            assert [r[0] for r in calls] == ["preflight"]
        else:
            result = await dispatch()
            if operation == "inventory":
                assert result == [{"id": 1, "value": 2.125}]
                retained = session.sync_session.evidence.originals["query-rows"][0]
                assert retained["sql_index"] == 0
                assert retained["statement"] == "SELECT * FROM execution_run_projection"
                assert retained["parameters"] == {"identity": 1}
                assert retained["read"]["preflight"]["snapshot"] == "fixture:1"
                assert retained["read"]["rows"] == 1
                assert [r[0] for r in calls] == ["preflight", "typed"]
                return
            if operation in ("stream", "stream_scalars"):
                values = await result.all()
            elif operation == "scalar":
                values = [result]
            else:
                values = result.all()
            value = (
                values[0] if operation in ("scalar", "scalars", "stream_scalars") else values[0][0]
            )
            assert isinstance(value, Projection)
            assert value.value == Decimal("2.125")
            assert [r[0] for r in calls] == ["preflight", "typed"]
            assert calls[0][1] is calls[1][1]
            assert calls[0][2] == calls[1][2] == {"identity": 1}
    mapper.dispose()
    engine.dispose()


@pytest.mark.parametrize(
    "shape", ["literal", "custom", "volatile", "lock", "unknown", "write", "legacy_session"]
)
def test_observer_rejects_uncovered_sql_before_driver(shape):
    from scripts.execution_capacity.observer_session import aggregate_statement
    from sqlalchemy import column, func, literal_column, table, text

    actual = table("execution_events", column("run_id"))
    statements = {
        "literal": select(literal_column("pg_sleep(10)")).select_from(actual),
        "custom": select(actual).where(actual.c.run_id.op("= ANY (SELECT pg_sleep(10)) --")("x")),
        "volatile": select(func.pg_sleep(1)).select_from(actual),
        "lock": select(actual).with_for_update(),
        "unknown": select(table("uncovered", column("id"))),
        "write": text("DELETE FROM execution_events"),
        "legacy_session": text(
            "SELECT id FROM sessions WHERE id=:session AND "
            "owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team "
            "AND deleted_at IS NULL"
        ),
    }
    with pytest.raises(ValueError, match="unsupported"):
        aggregate_statement(statements[shape])


@pytest.mark.asyncio
async def test_actual_dispatch_keeps_only_latest_pair_and_original_sql_order(monkeypatch):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.observer_session import ObserverSession

    from app.infrastructure.execution.original_evidence import retain_read

    engine = create_engine("sqlite://")
    table = Table("execution_run_projection", MetaData(), Column("id", Integer, primary_key=True))
    table.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), [{"id": 1}, {"id": 2}])
    original = Connection.execute

    def injected(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            return original(
                connection,
                select(
                    __import__("sqlalchemy").literal(1).label("row_count"),
                    __import__("sqlalchemy").literal(20).label("max_bytes"),
                    __import__("sqlalchemy").literal(20).label("total_bytes"),
                    __import__("sqlalchemy").literal("on").label("read_only"),
                    __import__("sqlalchemy").literal("repeatable read").label("isolation"),
                    __import__("sqlalchemy").literal("fixture:1").label("snapshot"),
                ),
            )
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", injected)
    owner = EvidenceOwner(budget=EvidenceBudget())

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    try:
        async with AsyncSession(sync_session_class=Bound) as session:
            first = await session.execute(select(table.c.id).where(table.c.id == 1))
            assert first.scalar_one() == 1
            sync = session.sync_session
            token, observation = sync.latest_sql(after=0)
            assert sync.sql_for_result(first) == (token, observation)
            retain_read(
                session, "version-source", "fixture-first", {"id": 1}, 1, source_result=first
            )
            assert owner.originals["version-source"][-1]["read"]["sql_index"] == 0
            with pytest.raises(ValueError, match="result missing or superseded"):
                sync.sql_for_result(object())
            assert sync.evidence.sql_ordinal(token, observation) == 0
            second = await session.execute(select(table.c.id).where(table.c.id == 2))
            assert second.scalar_one() == 2
            next_token, next_observation = sync.latest_sql(after=1)
            assert sync.sql_for_result(second) == (next_token, next_observation)
            with pytest.raises(ValueError, match="source Result required"):
                retain_read(session, "version-source", "fixture-first", {"id": 1}, 1)
            with pytest.raises(ValueError, match="result missing or superseded"):
                retain_read(
                    session, "version-source", "fixture-first", {"id": 1}, 1, source_result=first
                )
            assert len(owner.originals["version-source"]) == 1
            retain_read(
                session, "version-source", "fixture-second", {"id": 2}, 2, source_result=second
            )
            assert owner.originals["version-source"][-1]["read"]["sql_index"] == 1
            with pytest.raises(ValueError, match="result missing or superseded"):
                sync.sql_for_result(first)
            sync.forget_result(first)
            assert sync.sql_for_result(second) == (next_token, next_observation)
            sync.forget_result(second)
            with pytest.raises(ValueError, match="result missing or superseded"):
                sync.sql_for_result(second)
            assert sync.evidence.sql_ordinal(next_token, next_observation) == 1
            assert sync.evidence.sql_ordinal(token, observation) == 0
            assert len(sync.evidence.sql_reads) == 2
            assert sync.evidence_dispatch_count == 2
            assert not hasattr(sync, "evidence_reads")
            assert not hasattr(sync, "evidence_tokens")
            with pytest.raises(ValueError, match="missing or ambiguous"):
                sync.latest_sql(after=0)
        async with AsyncSession(sync_session_class=Bound) as second_session:
            third = await second_session.execute(select(table.c.id).where(table.c.id == 1))
            assert third.scalar_one() == 1
            second_sync = second_session.sync_session
            third_token, third_observation = second_sync.latest_sql(after=0)
            assert owner.sql_ordinal(third_token, third_observation) == 2
            assert [record["uow"] for record in owner.sql_reads] == [0, 0, 1]
            assert second_sync.evidence_dispatch_count == 1
    finally:
        engine.dispose()
