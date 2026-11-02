"""Scoped immutable completed checkpoints and revision-fenced heads."""

import sqlalchemy as sa

from alembic import op
from app.infrastructure.execution.context_checkpoint_models import metadata

revision = "0039context_checkpoint"
down_revision = "0038context_summary"
branch_labels = None
depends_on = None


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
    metadata.create_all(bind, checkfirst=False)
    trusted = "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    for table in metadata.sorted_tables:
        name = quote(table.name)
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY checkpoint_kernel ON {name} FOR ALL TO {quote(owner)},{quote(kernel)} USING ({trusted}) WITH CHECK ({trusted})"
            )
        )
        privileges = "SELECT,INSERT,UPDATE" if table.name.endswith("heads") else "SELECT,INSERT"
        bind.execute(sa.text(f"GRANT {privileges} ON {name} TO {quote(kernel)}"))
    bind.execute(
        sa.text("""
CREATE TRIGGER context_checkpoint_immutable BEFORE UPDATE OR DELETE ON public.execution_context_checkpoints
FOR EACH ROW EXECUTE FUNCTION public.opencitadel_context_summary_immutable();
""")
    )


def downgrade():
    raise RuntimeError("checkpoints cannot be erased; rebuild an owned development database")
