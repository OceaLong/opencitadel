"""Start the short analysis capture cache window when metrics are sealed."""

import runpy
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0024analysis_sealed_cache"
down_revision = "0023analysis_runtime_repairs"
branch_labels = None
depends_on = None


def _capture_sql() -> str:
    predecessor = runpy.run_path(str(Path(__file__).with_name("0023analysis_runtime_repairs.py")))
    sql = predecessor["_analysis_capture_sql"]()
    old = "c.captured_at>clock_timestamp()-interval '30 seconds'"
    if sql.count(old) != 1:
        raise RuntimeError("expected one analysis capture cache predicate")
    return sql.replace(old, "m.sealed_at>clock_timestamp()-interval '30 seconds'")


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote

    # A two-step addition leaves pre-existing sealed rows NULL, so migration
    # time cannot make an old snapshot look freshly cached.
    bind.execute(
        sa.text("ALTER TABLE public.analysis_capture_metrics ADD COLUMN sealed_at timestamptz")
    )
    bind.execute(
        sa.text(
            "ALTER TABLE public.analysis_capture_metrics ALTER COLUMN sealed_at SET DEFAULT clock_timestamp()"
        )
    )
    bind.execute(sa.text(_capture_sql()))

    capture = "public.opencitadel_analysis_capture(text,text)"
    bind.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {capture} FROM PUBLIC,{quote(api)},{quote(kernel)}")
    )
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {capture} TO {quote(api)}"))


def downgrade():
    raise RuntimeError("analysis sealed cache downgrade is unsupported")
