"""Make the immutable KB version parent pointer safe for retention GC.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "fk_knowledge_base_versions_parent_owner"
_CONCURRENT_INDEXES = (
    (
        "ix_session_resource_bindings_resource_version",
        "session_resource_bindings",
        "(resource_kind, resource_id, version_id)",
    ),
    (
        "ix_resource_builds_resource_version_state",
        "resource_builds",
        "(resource_kind, resource_id, version_id, state)",
    ),
)


def _get_index_validity(name: str) -> Union[bool, None]:
    """Return None/missing, False/invalid, or True/valid."""
    context = op.get_context()
    if context.as_sql:
        return None
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT index_state.indisvalid
            FROM pg_index AS index_state
            JOIN pg_class AS index_class
              ON index_class.oid = index_state.indexrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = index_class.relnamespace
            WHERE index_class.relname = :index_name
              AND namespace.nspname = current_schema()
            """
        ),
        {"index_name": name},
    ).first()
    return None if row is None else bool(row[0])


def _create_concurrent_indexes() -> None:
    context = op.get_context()
    for name, table, expression in _CONCURRENT_INDEXES:
        validity = _get_index_validity(name)
        if validity is True:
            continue
        if validity is False:
            with context.autocommit_block():
                op.execute(
                    sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                )
        with context.autocommit_block():
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                    f"{name} ON {table} {expression}"
                )
            )


def _drop_concurrent_indexes() -> None:
    context = op.get_context()
    for name, _table, _expression in reversed(_CONCURRENT_INDEXES):
        with context.autocommit_block():
            op.execute(
                sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            )


def upgrade() -> None:
    op.drop_constraint(
        _CONSTRAINT,
        "knowledge_base_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        _CONSTRAINT,
        "knowledge_base_versions",
        "knowledge_base_versions",
        ["parent_version_id", "knowledge_base_id"],
        ["id", "knowledge_base_id"],
        ondelete="SET NULL (parent_version_id)",
        deferrable=True,
        initially="DEFERRED",
    )
    _create_concurrent_indexes()


def downgrade() -> None:
    _drop_concurrent_indexes()
    op.drop_constraint(
        _CONSTRAINT,
        "knowledge_base_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        _CONSTRAINT,
        "knowledge_base_versions",
        "knowledge_base_versions",
        ["parent_version_id", "knowledge_base_id"],
        ["id", "knowledge_base_id"],
        deferrable=True,
        initially="DEFERRED",
    )
