"""Add execution views, replay observations, provenance and usage facts."""

from alembic import op
from app.infrastructure.migrations.execution_view_ddl import upgrade_execution_view

revision = "0002execution_view"
down_revision = "0001greenfield"
branch_labels = None
depends_on = None


def upgrade():
    upgrade_execution_view(op.get_bind())


def downgrade():
    raise RuntimeError(
        "execution view downgrade would discard durable provenance and observations; restore a backup instead"
    )
