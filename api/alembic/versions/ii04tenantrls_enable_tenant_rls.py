"""enable tenant row level security

Revision ID: ii04tenantrls
Revises: hh03tenantteams
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op

from app.infrastructure.security.tenant_rls import (
    CHILD_TABLES,
    CONFIG_ROOT_TABLES,
    PRIVATE_ROOT_TABLES,
    VISIBILITY_ROOT_TABLES,
    child_predicate,
    config_select_predicate,
    config_write_predicate,
    disable_policy_statements,
    policy_statements,
)

revision: str = "ii04tenantrls"
down_revision: Union[str, None] = "hh03tenantteams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table_name in sorted(PRIVATE_ROOT_TABLES):
        for statement in policy_statements(table_name):
            op.execute(statement)
    for table_name in sorted(VISIBILITY_ROOT_TABLES):
        for statement in policy_statements(table_name, has_visibility=True):
            op.execute(statement)
    for table_name in sorted(CONFIG_ROOT_TABLES):
        for statement in policy_statements(
            table_name,
            select_predicate=config_select_predicate(),
            write_predicate=config_write_predicate(),
        ):
            op.execute(statement)
    for table_name, (parent, foreign_key, parent_key) in sorted(CHILD_TABLES.items()):
        predicate = child_predicate(table_name, parent, foreign_key, parent_key)
        for statement in policy_statements(
            table_name,
            inherited_predicate=predicate,
        ):
            op.execute(statement)


def downgrade() -> None:
    tables = (
        set(CHILD_TABLES)
        | CONFIG_ROOT_TABLES
        | PRIVATE_ROOT_TABLES
        | VISIBILITY_ROOT_TABLES
    )
    for table_name in sorted(tables, reverse=True):
        for statement in disable_policy_statements(table_name):
            op.execute(statement)
