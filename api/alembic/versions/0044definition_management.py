"""Durable administrator requests; only the kernel executes retirement replay."""

import sqlalchemy as sa

from alembic import op

revision = "0044definition_management"
down_revision = "0043context_content_dependencies"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    quote = bind.dialect.identifier_preparer.quote
    # All management writes now pass through the kernel's durable request path.
    op.execute(
        f"REVOKE INSERT,UPDATE ON execution_definition_heads,execution_definition_changes FROM {quote(api)}"
    )
    op.execute("""
CREATE TABLE execution_definition_requests(
 request_id uuid PRIMARY KEY, actor_user_id varchar(255) NOT NULL,
 body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
 status varchar(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed')),
 result jsonb, created_at timestamptz NOT NULL DEFAULT clock_timestamp(), completed_at timestamptz,
 CHECK ((status='pending' AND result IS NULL AND completed_at IS NULL)
     OR (status<>'pending' AND result IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX ix_definition_requests_pending ON execution_definition_requests(created_at,request_id) WHERE status='pending';
ALTER TABLE execution_definition_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_definition_requests FORCE ROW LEVEL SECURITY;
""")
    valid = "public.opencitadel_authorization_valid()"
    admin = f"{valid} AND current_setting('app.auth_mode',true)='user' AND current_setting('app.is_admin',true)='true'"
    system = f"{valid} AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    op.execute(
        f"CREATE POLICY definition_request_read ON execution_definition_requests FOR SELECT TO {quote(api)} USING ({admin})"
    )
    op.execute(
        f"CREATE POLICY definition_request_submit ON execution_definition_requests FOR INSERT TO {quote(api)} WITH CHECK ({admin} AND actor_user_id=current_setting('app.user_id',true) AND status='pending' AND result IS NULL)"
    )
    op.execute(
        f"CREATE POLICY definition_request_kernel ON execution_definition_requests FOR ALL TO {quote(kernel)} USING ({system}) WITH CHECK ({system})"
    )
    op.execute(
        f"REVOKE ALL ON execution_definition_requests FROM PUBLIC,{quote(api)},{quote(kernel)}"
    )
    op.execute(f"GRANT SELECT,INSERT ON execution_definition_requests TO {quote(api)}")
    op.execute(f"GRANT SELECT,UPDATE ON execution_definition_requests TO {quote(kernel)}")
    op.execute("""
CREATE FUNCTION public.opencitadel_definition_request_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status<>'pending' OR
    ROW(OLD.request_id,OLD.actor_user_id,OLD.body,OLD.created_at) IS DISTINCT FROM
    ROW(NEW.request_id,NEW.actor_user_id,NEW.body,NEW.created_at)
 THEN RAISE EXCEPTION 'definition request is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER definition_request_immutable BEFORE UPDATE OR DELETE ON execution_definition_requests
 FOR EACH ROW EXECUTE FUNCTION public.opencitadel_definition_request_immutable();
""")


def downgrade():
    raise RuntimeError(
        "definition audit requests are retained; rebuild an owned development database"
    )
