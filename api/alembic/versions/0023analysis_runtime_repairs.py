"""Repair analysis, comparison, and export functions in place.

The historical migrations are immutable. Rebuild the affected functions from
their original DDL, with exact guards for each targeted correction.
"""

import runpy
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0023analysis_runtime_repairs"
down_revision = "0022analysis_point_facts"
branch_labels = None
depends_on = None


def _historical(filename: str, name: str) -> str:
    return runpy.run_path(str(Path(__file__).with_name(filename)))[name]


def _replace_exact(sql: str, old: str, new: str, count: int = 1) -> str:
    found = sql.count(old)
    if found != count:
        raise RuntimeError(f"expected {count} occurrences of {old!r}, found {found}")
    return sql.replace(old, new)


def _analysis_capture_sql() -> str:
    sql = _historical("0016execution_analysis.py", "CAPTURE")
    sql = _replace_exact(
        sql,
        "CREATE FUNCTION public.opencitadel_analysis_capture(encoded text, signature text) RETURNS jsonb",
        "CREATE OR REPLACE FUNCTION public.opencitadel_analysis_capture(encoded text, signature text) RETURNS jsonb",
    )
    sql = _replace_exact(
        sql,
        "epoch bigint; capture_id uuid;",
        "epoch bigint; capture_token uuid;",
    )
    sql = _replace_exact(sql, " capture_id:=", " capture_token:=", 2)
    return _replace_exact(sql, "opencitadel_analysis_capture.capture_id", "capture_token", 16)


def _analysis_point_bindings_sql() -> str:
    historical = _historical("0017execution_comparisons.py", "RESOURCE_BINDINGS")
    historical = _replace_exact(
        historical,
        "UNION SELECT run_id,kind,id,version,true AS pinned FROM jsonb_to_recordset(CAST(resources_json AS jsonb)) AS j(run_id uuid,kind text,id text,version text)",
        "UNION SELECT j.run_id,j.kind,j.id,j.version,true AS pinned FROM jsonb_to_recordset(CAST(resources_json AS jsonb)) AS j(run_id uuid,kind text,id text,version text)",
    )
    return (
        "CREATE OR REPLACE FUNCTION public.opencitadel_analysis_point_bindings(scope_value text,capture_id_value uuid,members_json jsonb,resources_json jsonb) RETURNS TABLE(run_id uuid,source jsonb,resources jsonb,pins jsonb,owners jsonb) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE owner_value text;team_value text; BEGIN team_value:=NULLIF(current_setting('app.team_id',true),'');owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true) ELSE NULL END; RETURN QUERY "
        + historical
        + "; END $$"
    )


def _export_accept_sql() -> str:
    sql = _historical("0020execution_exports.py", "ACCEPT")
    sql = _replace_exact(
        sql,
        "CREATE FUNCTION public.opencitadel_export_accept(encoded text,signature text,source_encoded text,source_signature text) RETURNS jsonb",
        "CREATE OR REPLACE FUNCTION public.opencitadel_export_accept(encoded text,signature text,source_encoded text,source_signature text) RETURNS jsonb",
    )
    return _replace_exact(sql, "(p->'request'-'request_id')", "((p->'request')-'request_id')")


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

    for sql in (
        _analysis_capture_sql(),
        _analysis_point_bindings_sql(),
        _export_accept_sql(),
    ):
        bind.execute(sa.text(sql))

    capture = "public.opencitadel_analysis_capture(text,text)"
    bindings = "public.opencitadel_analysis_point_bindings(text,uuid,jsonb,jsonb)"
    accept = "public.opencitadel_export_accept(text,text,text,text)"
    bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {capture} FROM PUBLIC"))
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {capture} TO {quote(api)}"))
    bind.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {bindings} FROM PUBLIC,{quote(api)},{quote(kernel)}")
    )
    bind.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {accept} FROM PUBLIC,{quote(api)},{quote(kernel)}")
    )
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {accept} TO {quote(api)}"))


def downgrade():
    raise RuntimeError("analysis authority runtime repair downgrade is unsupported")
