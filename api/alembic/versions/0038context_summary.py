"""Kernel-only immutable recovery evidence for separately billed summaries."""

import sqlalchemy as sa

from alembic import op
from app.infrastructure.execution.context_summary_models import metadata

revision = "0038context_summary"
down_revision = "0037context_sources"
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
    name = "execution_context_summary_proposals"
    bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
    trusted = "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    bind.execute(
        sa.text(
            f"CREATE POLICY summary_kernel ON {name} FOR ALL TO {quote(owner)},{quote(kernel)} USING ({trusted}) WITH CHECK ({trusted})"
        )
    )
    bind.execute(sa.text(f"GRANT SELECT,INSERT ON {name} TO {quote(kernel)}"))
    bind.execute(
        sa.text("""
CREATE FUNCTION public.opencitadel_context_summary_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'context_summary_immutable'; END $$;
CREATE TRIGGER context_summary_immutable BEFORE UPDATE OR DELETE ON public.execution_context_summary_proposals
FOR EACH ROW EXECUTE FUNCTION public.opencitadel_context_summary_immutable();
""")
    )


def downgrade():
    raise RuntimeError("summary receipts cannot be erased; rebuild an owned development database")
