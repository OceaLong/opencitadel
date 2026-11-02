"""Freeze the checkpoint revision with each immutable accepted source cut."""

import sqlalchemy as sa

from alembic import op

revision = "0040context_cut_checkpoint"
down_revision = "0039context_checkpoint"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    owner = bind.execute(sa.text("SELECT current_user")).scalar_one()
    quote = bind.dialect.identifier_preparer.quote
    # Only the narrowly scoped definer can read a user's checkpoint revision.
    # API callers receive no table privilege or mutation capability.
    bind.execute(
        sa.text(f"""
CREATE POLICY checkpoint_cut_owner ON public.execution_context_checkpoint_heads
FOR SELECT TO {quote(owner)} USING (public.opencitadel_context_scope_allowed(scope_key))
""")
    )
    bind.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION public.opencitadel_context_cut(scope_value text,collection_value text,request_value uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE saved jsonb; position_value bigint; revision_value bigint;
BEGIN
 IF NOT public.opencitadel_context_scope_allowed(scope_value) OR length(collection_value) NOT BETWEEN 1 AND 255
 THEN RAISE EXCEPTION 'context_scope_denied' USING ERRCODE='42501'; END IF;
 IF request_value IS NOT NULL THEN
   PERFORM pg_advisory_xact_lock(hashtextextended('context-cut:'||scope_value||':'||request_value::text,0));
   SELECT body INTO saved FROM public.execution_context_cuts WHERE scope_key=scope_value AND request_id=request_value;
   IF saved IS NOT NULL THEN
     IF saved->>'collection_id'<>collection_value THEN RAISE EXCEPTION 'context_request_conflict'; END IF;
     RETURN saved;
   END IF;
 END IF;
 SELECT s.head_position,h.revision INTO position_value,revision_value
 FROM (VALUES(1)) anchor(n)
 LEFT JOIN public.execution_scope_head s ON s.owner_scope_key=scope_value
 LEFT JOIN public.execution_context_checkpoint_heads h ON h.scope_key=scope_value AND h.collection_id=collection_value;
 saved=jsonb_build_object('format_version',1,'scope_key',scope_value,'collection_id',collection_value,
                         'through_position',COALESCE(position_value,0),'checkpoint_revision',COALESCE(revision_value,0));
 IF request_value IS NOT NULL THEN
   INSERT INTO public.execution_context_cuts(scope_key,request_id,body) VALUES(scope_value,request_value,saved);
 END IF;
 RETURN saved;
END $$;
""")
    )


def downgrade():
    raise RuntimeError("accepted cuts are immutable; rebuild an owned development database")
