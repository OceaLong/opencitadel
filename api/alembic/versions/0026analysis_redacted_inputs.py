"""Keep redacted content outside readable analysis resource sets.

Their immutable redacted metadata remains in the capture fingerprint, while
explicit pins and all other content retain the existing availability checks.
"""

import runpy
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0026analysis_redacted_inputs"
down_revision = "0025analysis_point_redaction"
branch_labels = None
depends_on = None


_CONTENT_JOIN = """FROM members m JOIN public.execution_content_bindings b ON b.scope_key=scope_value AND b.run_id=m.run_id AND b.formal_position<=m.formal_position
 JOIN public.execution_public_content c ON c.scope_key=b.scope_key AND c.content_id=b.content_id)"""
_CONTENT_FILTER = """FROM members m JOIN public.execution_content_bindings b ON b.scope_key=scope_value AND b.run_id=m.run_id AND b.formal_position<=m.formal_position
 JOIN public.execution_public_content c ON c.scope_key=b.scope_key AND c.content_id=b.content_id
 WHERE NOT c.redacted)"""

# Recorded content belongs to the recording's source run, not to the selected
# evaluation run. Its pin must remain in the immutable recording and in
# resource_pins for replay, but redacted input or output is not readable.
_REDACTED_RECORDING_PINS = """redacted_recording_pins AS (
 SELECT DISTINCT o.run_id,o.id AS owner_id,c.content_id::text AS id,c.content_digest AS version
 FROM version_owners o
 JOIN public.evaluation_recording_versions v ON o.kind='recording_version'
  AND v.scope_key=scope_value AND v.id::text=o.id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(v.body->'pins','[]'::jsonb)) pin
 JOIN public.execution_public_content c ON c.scope_key=scope_value
  AND c.content_id::text=pin->>'resource_id'
  AND c.content_digest=pin->>'resource_version'
 JOIN public.execution_content_bindings b ON b.scope_key=c.scope_key
  AND b.content_id=c.content_id AND b.run_id::text=v.body->>'source_run_id'
 WHERE pin->>'resource_kind'='execution_content'
  AND c.redacted AND c.phase=b.phase AND c.phase IN ('input','output')),
fixed_refs AS ("""

_FIXED_RECORDING_PINS = """CROSS JOIN LATERAL jsonb_array_elements(COALESCE(v.body->'pins','[]'::jsonb)) e
 WHERE NOT EXISTS(SELECT 1 FROM redacted_recording_pins redacted
  WHERE redacted.run_id=o.run_id AND redacted.owner_id=o.id
   AND e->>'resource_kind'='execution_content'
   AND redacted.id=e->>'resource_id' AND redacted.version=e->>'resource_version')),
refs AS ("""

_REQUIRED_PIN_FILTER = """AND NOT EXISTS(SELECT 1 FROM redacted_recording_pins redacted
  WHERE p.owner_kind='recording_version' AND redacted.run_id=r.run_id
   AND redacted.owner_id=p.owner_id AND p.resource_kind='execution_content'
   AND redacted.id=p.resource_id AND redacted.version=p.resource_version)"""


def _historical(filename: str, name: str) -> str:
    return runpy.run_path(str(Path(__file__).with_name(filename)))[name]


def _replace_once(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise RuntimeError(f"analysis predecessor mismatch: {old[:80]!r}")
    return sql.replace(old, new)


def _filter_recording_pins(sql: str) -> str:
    sql = _replace_once(sql, "fixed_refs AS (", _REDACTED_RECORDING_PINS)
    sql = _replace_once(
        sql,
        "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(v.body->'pins','[]'::jsonb)) e),\nrefs AS (",
        _FIXED_RECORDING_PINS,
    )
    return _replace_once(
        sql,
        "EXISTS(SELECT 1 FROM version_owners o WHERE o.run_id=r.run_id AND o.kind=p.owner_kind AND o.id=p.owner_id))\n UNION SELECT m.run_id,'execution_content'",
        "EXISTS(SELECT 1 FROM version_owners o WHERE o.run_id=r.run_id AND o.kind=p.owner_kind AND o.id=p.owner_id))\n "
        + _REQUIRED_PIN_FILTER
        + "\n UNION SELECT m.run_id,'execution_content'",
    )


def _manifest_sql() -> str:
    sql = _historical("0016execution_analysis.py", "CAPTURE_MANIFEST")
    sql = _replace_once(
        sql,
        "CREATE FUNCTION public.opencitadel_analysis_manifest(encoded text, signature text, members_json jsonb)",
        "CREATE OR REPLACE FUNCTION public.opencitadel_analysis_manifest(encoded text, signature text, members_json jsonb)",
    )
    sql = _replace_once(sql, _CONTENT_JOIN, _CONTENT_FILTER)
    sql = _filter_recording_pins(sql)
    sql = _replace_once(
        sql,
        "SELECT r.run_id,r.visible AND COALESCE(bool_and(c.available),true)\n AND NOT EXISTS(SELECT 1 FROM version_owners",
        """SELECT r.run_id,r.visible AND COALESCE(bool_and(c.available),true)
 AND NOT EXISTS(SELECT 1 FROM redacted_recording_pins redacted
  WHERE redacted.run_id=r.run_id AND NOT EXISTS(
   SELECT 1 FROM public.resource_pins pin WHERE pin.scope_key=scope_value
    AND pin.owner_kind='recording_version' AND pin.owner_id=redacted.owner_id
    AND pin.resource_kind='execution_content' AND pin.resource_id=redacted.id
    AND pin.resource_version=redacted.version AND pin.available))
 AND NOT EXISTS(SELECT 1 FROM version_owners""",
    )
    # Retain a fingerprint of the excluded redacted *public* metadata. Changing
    # or deleting one still invalidates an existing analysis/comparison capture.
    sql = _replace_once(
        sql,
        "'resources',COALESCE(jsonb_agg(jsonb_build_array(c.kind,c.id,c.version,c.pinned,c.available) ORDER BY c.kind,c.id,c.version) FILTER(WHERE c.id IS NOT NULL),'[]'::jsonb)",
        """'redacted_content_bindings',COALESCE((SELECT jsonb_agg(
   jsonb_build_array(b.step_id,b.phase,b.formal_position,c.content_id,c.content_digest)
   ORDER BY b.formal_position,b.step_id,c.content_id)
   FROM public.execution_content_bindings b
   JOIN public.execution_public_content c ON c.scope_key=b.scope_key AND c.content_id=b.content_id
   WHERE b.scope_key=scope_value AND b.run_id=r.run_id AND b.formal_position<=
    (SELECT max(formal_position) FROM members member WHERE member.run_id=r.run_id)
    AND c.redacted),'[]'::jsonb),
 'resources',COALESCE(jsonb_agg(jsonb_build_array(c.kind,c.id,c.version,c.pinned,c.available) ORDER BY c.kind,c.id,c.version) FILTER(WHERE c.id IS NOT NULL),'[]'::jsonb)""",
    )
    return _replace_once(
        sql,
        "'resources',COALESCE(jsonb_agg(jsonb_build_array(c.kind,c.id,c.version,c.pinned,c.available) ORDER BY c.kind,c.id,c.version) FILTER(WHERE c.id IS NOT NULL),'[]'::jsonb)",
        """'redacted_recording_pins',COALESCE((SELECT jsonb_agg(
  jsonb_build_array(pin.owner_id,pin.id,pin.version,EXISTS(
   SELECT 1 FROM public.resource_pins live WHERE live.scope_key=scope_value
    AND live.owner_kind='recording_version' AND live.owner_id=pin.owner_id
    AND live.resource_kind='execution_content' AND live.resource_id=pin.id
    AND live.resource_version=pin.version AND live.available))
  ORDER BY pin.owner_id,pin.id,pin.version)
  FROM redacted_recording_pins pin WHERE pin.run_id=r.run_id),'[]'::jsonb),
 'resources',COALESCE(jsonb_agg(jsonb_build_array(c.kind,c.id,c.version,c.pinned,c.available) ORDER BY c.kind,c.id,c.version) FILTER(WHERE c.id IS NOT NULL),'[]'::jsonb)""",
    )


def _materialize_sql() -> str:
    parts = _historical("0017execution_comparisons.py", "CREATE_PREFIX")
    parts = _replace_once(
        parts,
        "CREATE FUNCTION public.opencitadel_comparison_materialize(encoded text, signature text)",
        "CREATE OR REPLACE FUNCTION public.opencitadel_comparison_materialize(encoded text, signature text)",
    )
    bindings = _replace_once(
        _historical("0017execution_comparisons.py", "RESOURCE_BINDINGS"),
        _CONTENT_JOIN,
        _CONTENT_FILTER,
    )
    bindings = _filter_recording_pins(bindings)
    return (
        parts
        + _historical("0017execution_comparisons.py", "SELECTION")
        + _historical("0017execution_comparisons.py", "CREATE_AFTER_SELECTION")
        + _historical("0017execution_comparisons.py", "INTERVAL_FACTS")
        + _historical("0017execution_comparisons.py", "CREATE_BETWEEN_FACTS")
        + _historical("0017execution_comparisons.py", "APPROVAL_FACTS")
        + _historical("0017execution_comparisons.py", "CREATE_BEFORE_BINDINGS")
        + bindings
        + _historical("0017execution_comparisons.py", "CREATE_SUFFIX")
    )


def _content_context_sql() -> str:
    sql = _historical("0021analysis_native_reads.py", "BODY_CONTEXT")
    sql = _replace_once(
        sql,
        "CREATE FUNCTION public.opencitadel_comparison_content_context(encoded text,signature text)",
        "CREATE OR REPLACE FUNCTION public.opencitadel_comparison_content_context(encoded text,signature text)",
    )
    return _replace_once(
        sql,
        "IF ref IS NULL OR ref->>'availability' IS DISTINCT FROM 'available' THEN RETURN NULL; END IF;",
        """IF ref IS NULL OR ref->>'availability' IS DISTINCT FROM 'available' THEN RETURN NULL; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.execution_public_content c
  JOIN public.execution_content_bindings b ON b.scope_key=c.scope_key AND b.content_id=c.content_id
  WHERE c.scope_key=s AND c.content_id::text=ref->>'content_id' AND NOT c.redacted
   AND b.run_id=member.run_id AND b.step_id=p->>'step_id'
   AND b.formal_position<=member.formal_position) THEN RETURN NULL; END IF;""",
    )


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
    for sql in (_manifest_sql(), _materialize_sql(), _content_context_sql()):
        bind.execute(sa.text(sql))
    for signature in (
        "public.opencitadel_analysis_manifest(text,text,jsonb)",
        "public.opencitadel_comparison_materialize(text,text)",
        "public.opencitadel_comparison_content_context(text,text)",
    ):
        bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO {quote(api)}"))


def downgrade():
    raise RuntimeError("redacted analysis input boundary downgrade is unsupported")
