"""Forward-owned private comparison table handles; never part of greenfield Base metadata.

0017 owns exact constraints, RLS and grants. Runtime access remains signed functions.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

comparison_sets = sa.Table(
    "comparison_sets",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("caller_id", sa.Text(), primary_key=False, nullable=False),
    sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

comparison_revisions = sa.Table(
    "comparison_revisions",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("comparison_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("caller_id", sa.Text(), primary_key=False, nullable=False),
    sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("query", JSONB(), primary_key=False, nullable=False),
    sa.Column("selection", JSONB(), primary_key=False, nullable=False),
    sa.Column("baseline_configuration", sa.Text(), primary_key=False, nullable=True),
    sa.Column("authority_revision", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
    sa.Column("metric_version", sa.Text(), primary_key=False, nullable=False),
    sa.Column("alignment_revision", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("published", sa.Boolean(), primary_key=False, nullable=False),
)

comparison_members = sa.Table(
    "comparison_members",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("ordinal", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("formal_position", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("progress_position", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("observed_order", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("projector_version", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("generation", sa.Text(), primary_key=False, nullable=False),
    sa.Column("coverage", JSONB(), primary_key=False, nullable=False),
    sa.Column("run_fact", JSONB(), primary_key=False, nullable=False),
    sa.Column("interval_fact", JSONB(), primary_key=False, nullable=True),
    sa.Column("approval_fact", JSONB(), primary_key=False, nullable=True),
)

comparison_accounting = sa.Table(
    "comparison_accounting",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("ordinal", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("formal_position", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("progress_position", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("observed_order", sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column("projector_version", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("generation", sa.Text(), primary_key=False, nullable=False),
)

comparison_usage = sa.Table(
    "comparison_usage",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("call_identity", sa.Text(), primary_key=True, nullable=False),
    sa.Column("body", JSONB(), primary_key=False, nullable=False),
)

comparison_scores = sa.Table(
    "comparison_scores",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("capture_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("body", JSONB(), primary_key=False, nullable=False),
)

comparison_accounting_links = sa.Table(
    "comparison_accounting_links",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("result_id", sa.UUID(), primary_key=True, nullable=False),
)

comparison_resources = sa.Table(
    "comparison_resources",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("source", JSONB(), primary_key=False, nullable=True),
    sa.Column("resources", JSONB(), primary_key=False, nullable=False),
    sa.Column("pins", JSONB(), primary_key=False, nullable=False),
    sa.Column("owners", JSONB(), primary_key=False, nullable=False),
)

comparison_details = sa.Table(
    "comparison_details",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("slot", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("body", JSONB(), primary_key=False, nullable=False),
)

comparison_alignment_pairs = sa.Table(
    "comparison_alignment_pairs",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("pair_key", sa.Text(), primary_key=True, nullable=False),
    sa.Column("left_run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("left_step_id", sa.Text(), primary_key=True, nullable=False),
    sa.Column("left_attempt_id", sa.Text(), primary_key=True, nullable=False),
    sa.Column("right_run_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("right_step_id", sa.Text(), primary_key=False, nullable=False),
    sa.Column("right_attempt_id", sa.Text(), nullable=False),
    sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("supersedes", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("author", sa.Text(), primary_key=False, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
    sa.Column("body", JSONB(), primary_key=False, nullable=False),
)

comparison_alignments = sa.Table(
    "comparison_alignments",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("capture_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("supersedes", sa.Integer(), primary_key=False, nullable=False),
    sa.Column("author", sa.Text(), primary_key=False, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
    sa.Column("body", JSONB(), primary_key=False, nullable=False),
)

comparison_artifacts = sa.Table(
    "comparison_artifacts",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("step_id", sa.Text(), primary_key=True, nullable=False),
    sa.Column("artifact_id", sa.Text(), primary_key=True, nullable=False),
    sa.Column("version", sa.Integer(), primary_key=True, nullable=False),
    sa.Column("provenance_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("content_digest", sa.Text(), primary_key=False, nullable=False),
    sa.Column("storage_key", sa.Text(), nullable=False),
)

comparison_allocations = sa.Table(
    "comparison_allocations",
    metadata,
    sa.Column("capture_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("body", JSONB(), primary_key=False, nullable=False),
)

comparison_diff_jobs = sa.Table(
    "comparison_diff_jobs",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("capture_id", sa.UUID(), primary_key=False, nullable=False),
    sa.Column("caller_id", sa.Text(), primary_key=False, nullable=False),
    sa.Column("principal", JSONB(), primary_key=False, nullable=False),
    sa.Column("owner_scope", JSONB(), primary_key=False, nullable=False),
    sa.Column("selection", JSONB(), primary_key=False, nullable=False),
    sa.Column("status", sa.Text(), primary_key=False, nullable=False),
    sa.Column("lease_token", sa.UUID(), primary_key=False, nullable=True),
    sa.Column("lease_until", sa.DateTime(timezone=True), primary_key=False, nullable=True),
    sa.Column("body", JSONB(), primary_key=False, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

comparison_diff_pages = sa.Table(
    "comparison_diff_pages",
    metadata,
    sa.Column("job_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("scope_key", sa.Text(), primary_key=False, nullable=False),
    sa.Column("ordinal", sa.Integer(), primary_key=True, nullable=False),
    sa.Column("body", sa.Text(), primary_key=False, nullable=False),
)

comparison_receipts = sa.Table(
    "comparison_receipts",
    metadata,
    sa.Column("scope_key", sa.Text(), primary_key=True),
    sa.Column("caller_id", sa.Text(), primary_key=True),
    sa.Column("request_id", sa.Text(), primary_key=True),
    sa.Column("fingerprint", sa.Text(), nullable=False),
    sa.Column("response", JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
