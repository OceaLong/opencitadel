"""Use team ownership for team sessions while retaining their creator metadata.

Sessions retain owner_user_id in a team workspace. Their authority is the team,
unlike content/file/knowledge resources whose scoped owner is normalized.
"""

import re

import sqlalchemy as sa

from alembic import op

revision = "0029analysis_team_sessions"
down_revision = "0028analysis_scoped_jit"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "public.opencitadel_analysis_manifest(text,text,jsonb)",
    "public.opencitadel_analysis_resources_available(text,text,jsonb)",
    "public.opencitadel_analysis_capture(text,text)",
    "public.opencitadel_analysis_point_bindings(text,uuid,jsonb,jsonb)",
    "public.opencitadel_comparison_materialize(text,text)",
    "public.opencitadel_comparison_resource_rows(text,uuid,uuid[])",
    "public.opencitadel_comparison_current_rows(text,uuid,uuid[])",
    "public.opencitadel_export_resource_rows(text,uuid,uuid[])",
    "public.opencitadel_export_current_rows(text,uuid,uuid[])",
)

_SESSION_OWNER = re.compile(
    r"(?P<prefix>(?:JOIN|FROM) public\.sessions (?P<alias>[st]) [^\n]*?)"
    r"(?P=alias)\.owner_user_id IS NOT DISTINCT FROM owner_value AND "
    r"(?P=alias)\.team_id IS NOT DISTINCT FROM (?P<team>team_value|team)\b"
)


def _repair_session_scope(definition: str) -> str:
    def repair(match):
        alias, team = match.group("alias", "team")
        return (
            match.group("prefix")
            + f"({team} IS NOT NULL OR {alias}.owner_user_id IS NOT DISTINCT FROM owner_value)"
            f" AND {alias}.team_id IS NOT DISTINCT FROM {team}"
        )

    repaired, count = _SESSION_OWNER.subn(repair, definition)
    if not count:
        raise RuntimeError("analysis session-scope predecessor mismatch")
    return repaired


def upgrade():
    bind = op.get_bind()
    # Recreate the installed definition, preserving all later redaction repairs,
    # function-local settings (including jit), owner and existing grants.
    for signature in FUNCTIONS:
        definition = bind.scalar(
            sa.text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
            {"signature": signature},
        )
        bind.execute(sa.text(_repair_session_scope(definition)))


def downgrade():
    raise RuntimeError("team session authority downgrade is unsupported")
