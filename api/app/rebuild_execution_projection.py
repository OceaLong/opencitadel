"""Rebuild one owner scope's formal execution projection (operator CLI, K4-1).

Usage::

    python -m app.rebuild_execution_projection --scope user:<user_id>
    python -m app.rebuild_execution_projection --scope team:<team_id>

The scope is marked ``rebuilding`` in ``execution_poisoned_scopes`` before the
projection is torn down, which (a) removes it from the kernel's pending-scope
discovery, and (b) turns "Run projection row missing" into a retryable defer
signal for the activity worker instead of a permanent policy failure. On
success the marker row is deleted, which also lifts any prior quarantine of the
scope. Run with the execution-kernel database credentials (the projection
tables and the quarantine table are kernel-writable).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.application.security.authorization_context import authorization_scope
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.models import ExecutionPoisonedScopeORM
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.storage.postgres import Postgres
from core.config import load_deployment_settings

_ACTOR = "rebuild:execution-projection"


def _parse_scope(raw: str) -> tuple[OwnerScope, str]:
    prefix, _, value = raw.partition(":")
    if prefix == "user" and value:
        return OwnerScope.personal(value), f"user:{value}"
    if prefix == "team" and value:
        return OwnerScope.team("execution-kernel", value), f"team:{value}"
    raise argparse.ArgumentTypeError("scope must be user:<user_id> or team:<team_id>")


async def _mark_rebuilding(session_factory, authorization, key, owner_scope) -> None:
    from app.infrastructure.security.db_authorization import configure_session_authorization

    now = datetime.now(UTC)
    async with session_factory() as session:
        await configure_session_authorization(session, authorization)
        await session.execute(
            pg_insert(ExecutionPoisonedScopeORM)
            .values(
                owner_scope_key=key,
                owner_user_id=owner_scope.user_id if owner_scope.team_id is None else None,
                team_id=owner_scope.team_id,
                reason="rebuilding",
                last_error="operator-driven projection rebuild in flight",
                failure_count=0,
                rebuilding=True,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=["owner_scope_key"],
                set_={"rebuilding": True, "last_seen_at": now},
            )
        )
        await session.commit()


async def _clear_marker(session_factory, authorization, key) -> None:
    from app.infrastructure.security.db_authorization import configure_session_authorization

    async with session_factory() as session:
        await configure_session_authorization(session, authorization)
        # Deleting the row both clears the rebuild marker and lifts any prior
        # quarantine: the freshly rebuilt scope re-enters pending discovery.
        await session.execute(
            delete(ExecutionPoisonedScopeORM).where(
                ExecutionPoisonedScopeORM.owner_scope_key == key
            )
        )
        await session.commit()


async def rebuild(raw_scope: str) -> int:
    owner_scope, key = _parse_scope(raw_scope)
    settings = load_deployment_settings()
    authorization = AuthorizationContext.system(_ACTOR)
    postgres = Postgres(settings)
    await postgres.init()
    try:
        with authorization_scope(authorization):
            session_factory = postgres.session_factory
            await _mark_rebuilding(session_factory, authorization, key, owner_scope)
            print(f"scope {key}: marked rebuilding; projection teardown + replay starting")
            projector = PostgresFormalProjector(
                session_factory=session_factory,
                authorization=authorization,
            )
            result = await projector.rebuild(owner_scope)
            await _clear_marker(session_factory, authorization, key)
            print(
                f"scope {key}: rebuilt {result.processed} event(s) through position "
                f"{result.last_position}; rebuild marker and quarantine cleared"
            )
    finally:
        await postgres.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.rebuild_execution_projection",
        description="Rebuild one owner scope's formal execution projection.",
    )
    parser.add_argument(
        "--scope",
        required=True,
        help="owner scope to rebuild: user:<user_id> or team:<team_id>",
    )
    args = parser.parse_args(argv)
    try:
        _parse_scope(args.scope)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    return asyncio.run(rebuild(args.scope))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "rebuild"]
