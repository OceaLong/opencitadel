"""Avoid expression compilation on bounded interactive analysis captures.

Empty captures still compile hundreds of manifest expressions with PostgreSQL
JIT enabled. Function-local settings also cover nested statements and restore
the caller's setting on exit; ownership, grants and authorization stay intact.
"""

import sqlalchemy as sa

from alembic import op

revision = "0028analysis_scoped_jit"
down_revision = "0027analysis_point_redaction"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "public.opencitadel_analysis_capture(text,text)",
    "public.opencitadel_analysis_manifest(text,text,jsonb)",
    "public.opencitadel_analysis_point_bindings(text,uuid,jsonb,jsonb)",
    "public.opencitadel_comparison_materialize(text,text)",
)


def upgrade():
    bind = op.get_bind()
    for signature in FUNCTIONS:
        bind.execute(sa.text(f"ALTER FUNCTION {signature} SET jit TO off"))


def downgrade():
    bind = op.get_bind()
    for signature in FUNCTIONS:
        bind.execute(sa.text(f"ALTER FUNCTION {signature} RESET jit"))
