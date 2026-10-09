"""Frozen revision 0002 schema. Do not derive historical DDL from live ORM metadata."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

execution_view_runs = sa.Table(
    "execution_view_runs",
    metadata,
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("family", sa.String(255), nullable=False),
    sa.Column("status", sa.String(255), nullable=False),
    sa.Column("wait_reason", sa.Text(), nullable=True),
    sa.Column("source", JSONB(), nullable=True),
    sa.Column("purpose", sa.String(255), nullable=False),
    sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("configuration_revision", sa.String(255), nullable=True),
    sa.Column("model_revision", sa.String(255), nullable=True),
    sa.Column("execution_mode", sa.String(255), nullable=True),
    sa.Column("configuration", JSONB(), nullable=True),
    sa.Column("public_summary", sa.Text(), nullable=True),
    sa.Column("completeness", JSONB(), nullable=False),
    sa.Column("capabilities", JSONB(), nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("projector_version", sa.Integer(), nullable=False),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
    sa.Column("latest_available", sa.DateTime(timezone=True), nullable=True),
    sa.Column("formal_position", sa.BigInteger(), nullable=False),
    sa.Column("progress_position", sa.BigInteger(), nullable=False),
    sa.Column("observed_order", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.UniqueConstraint("run_id", "scope_key", name="uq_execution_view_runs_scope"),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_runs_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_runs_schema_version"),
    sa.Index("ix_execution_view_runs_scope_time", "scope_key", "created_at"),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_runs_projection_revision"
    ),
    sa.CheckConstraint("formal_position >= 0", name="ck_execution_view_runs_formal_position"),
    sa.CheckConstraint("progress_position >= 0", name="ck_execution_view_runs_progress_position"),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_runs_observed_order"),
    sa.Index("ix_execution_view_runs_admitted", "scope_key", "admitted_at", "run_id"),
    sa.Index("ix_execution_view_runs_status", "scope_key", "status", "admitted_at"),
    sa.Index(
        "ix_execution_view_runs_configuration",
        "scope_key",
        "configuration_revision",
        "execution_mode",
        "admitted_at",
    ),
)

execution_view_steps = sa.Table(
    "execution_view_steps",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("step_id", sa.String(255), nullable=False),
    sa.Column("activity_id", sa.UUID(), nullable=True),
    sa.Column("invocation_id", sa.UUID(), nullable=True),
    sa.Column("attempt_id", sa.String(255), nullable=True),
    sa.Column("logical_step_id", sa.String(255), nullable=True),
    sa.Column("parent_step_id", sa.String(255), nullable=True),
    sa.Column("semantic_key", sa.String(255), nullable=True),
    sa.Column("relationship", sa.String(255), nullable=False),
    sa.Column("kind", sa.String(255), nullable=False),
    sa.Column("status", sa.String(255), nullable=False),
    sa.Column("wait_reason", sa.Text(), nullable=True),
    sa.Column("end_reason", sa.Text(), nullable=True),
    sa.Column("business_outcome", sa.String(255), nullable=True),
    sa.Column("tool_name", sa.String(255), nullable=True),
    sa.Column("tool_contract_revision", sa.String(255), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("duration_ms", sa.BigInteger(), nullable=True),
    sa.Column("first_persisted_output_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("public_summary", sa.Text(), nullable=True),
    sa.Column("input_ref", JSONB(), nullable=True),
    sa.Column("output_ref", JSONB(), nullable=True),
    sa.Column("artifact_refs", JSONB(), nullable=True),
    sa.Column("citation_refs", JSONB(), nullable=True),
    sa.Column("configuration", JSONB(), nullable=True),
    sa.Column("completeness", JSONB(), nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("observed_order", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_steps_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_steps_schema_version"),
    sa.Index("ix_execution_view_steps_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_view_steps_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("duration_ms >= 0", name="ck_execution_view_steps_duration_ms"),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_steps_projection_revision"
    ),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_steps_observed_order"),
    sa.UniqueConstraint("run_id", "step_id", name="uq_execution_view_steps_step"),
    sa.UniqueConstraint("run_id", "step_id", "attempt_id", name="uq_execution_view_steps_attempt"),
    sa.Index(
        "ix_execution_view_steps_parent", "run_id", "parent_step_id", "started_at", "attempt_id"
    ),
    sa.Index("ix_execution_view_steps_order", "run_id", "observed_order", "step_id"),
)

execution_view_checkpoints = sa.Table(
    "execution_view_checkpoints",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("boundary", sa.BigInteger(), nullable=False),
    sa.Column("projector_version", sa.Integer(), nullable=False),
    sa.Column("formal_position", sa.BigInteger(), nullable=False),
    sa.Column("progress_position", sa.BigInteger(), nullable=False),
    sa.Column("observed_order", sa.BigInteger(), nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("state_ref", JSONB(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_checkpoints_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_checkpoints_schema_version"),
    sa.Index("ix_execution_view_checkpoints_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_view_checkpoints_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("boundary >= 0", name="ck_execution_view_checkpoints_boundary"),
    sa.CheckConstraint(
        "formal_position >= 0", name="ck_execution_view_checkpoints_formal_position"
    ),
    sa.CheckConstraint(
        "progress_position >= 0", name="ck_execution_view_checkpoints_progress_position"
    ),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_checkpoints_observed_order"),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_checkpoints_projection_revision"
    ),
    sa.UniqueConstraint(
        "run_id", "boundary", "projector_version", name="uq_execution_view_checkpoints_boundary"
    ),
    sa.Index(
        "ix_execution_view_checkpoints_order", "run_id", "observed_order", "projector_version"
    ),
)

execution_view_observations = sa.Table(
    "execution_view_observations",
    metadata,
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("observed_order", sa.BigInteger(), primary_key=True, nullable=False),
    sa.Column("source_kind", sa.String(255), nullable=False),
    sa.Column("source_identity", sa.String(255), nullable=False),
    sa.Column("event_id", sa.UUID(), nullable=True),
    sa.Column("formal_position", sa.BigInteger(), nullable=False),
    sa.Column("progress_position", sa.BigInteger(), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("projector_version", sa.Integer(), nullable=False),
    sa.Column("public_payload", JSONB(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_observations_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_observations_schema_version"),
    sa.Index("ix_execution_view_observations_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_view_observations_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_observations_observed_order"),
    sa.CheckConstraint(
        "formal_position >= 0", name="ck_execution_view_observations_formal_position"
    ),
    sa.CheckConstraint(
        "progress_position >= 0", name="ck_execution_view_observations_progress_position"
    ),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_observations_projection_revision"
    ),
    sa.UniqueConstraint(
        "run_id",
        "source_kind",
        "source_identity",
        "projector_version",
        name="uq_execution_view_observations_source",
    ),
    sa.Index("ix_execution_view_observations_time", "run_id", "observed_at", "observed_order"),
)

artifact_version_provenance = sa.Table(
    "artifact_version_provenance",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("artifact_id", sa.String(255), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("producer_identity", sa.String(255), nullable=False),
    sa.Column("producer_run_id", sa.UUID(), nullable=True),
    sa.Column("producer_step_ids", JSONB(), nullable=True),
    sa.Column("activity_id", sa.UUID(), nullable=True),
    sa.Column("attempt_id", sa.String(255), nullable=True),
    sa.Column("invocation_id", sa.UUID(), nullable=True),
    sa.Column("produced_event_id", sa.UUID(), nullable=True),
    sa.Column("evidence_kind", sa.String(255), nullable=False),
    sa.Column("evidence", JSONB(), nullable=True),
    sa.Column("binding_status", sa.String(255), nullable=False),
    sa.Column("boundary", sa.BigInteger(), nullable=True),
    sa.Column("content_ref", JSONB(), nullable=True),
    sa.Column("content_digest", sa.String(255), nullable=True),
    sa.Column("citation_refs", JSONB(), nullable=True),
    sa.Column("availability", sa.String(255), nullable=False),
    sa.Column("revision", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_artifact_version_provenance_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_artifact_version_provenance_schema_version"),
    sa.Index("ix_artifact_version_provenance_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["producer_run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_artifact_version_provenance_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("boundary >= 0", name="ck_artifact_version_provenance_boundary"),
    sa.CheckConstraint("revision >= 0", name="ck_artifact_version_provenance_revision"),
    sa.UniqueConstraint(
        "artifact_id",
        "version",
        "producer_identity",
        name="uq_artifact_version_provenance_producer",
    ),
    sa.Index("ix_artifact_version_provenance_run_boundary", "producer_run_id", "boundary"),
    sa.CheckConstraint("version > 0", name="ck_artifact_version_provenance_version"),
    sa.CheckConstraint(
        "binding_status IN ('pending', 'bound', 'unavailable')",
        name="ck_artifact_version_provenance_binding",
    ),
    sa.CheckConstraint(
        "binding_status <> 'bound' OR producer_run_id IS NOT NULL",
        name="ck_artifact_version_provenance_bound_run",
    ),
    sa.CheckConstraint(
        "evidence_kind IN ('direct', 'derived', 'unknown')",
        name="ck_artifact_version_provenance_evidence_kind",
    ),
)

execution_usage_facts = sa.Table(
    "execution_usage_facts",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("call_identity", sa.String(255), nullable=False),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("activity_id", sa.UUID(), nullable=True),
    sa.Column("attempt_id", sa.String(255), nullable=True),
    sa.Column("purpose", sa.String(255), nullable=False),
    sa.Column("model_revision", sa.String(255), nullable=True),
    sa.Column("input_tokens", sa.BigInteger(), nullable=True),
    sa.Column("output_tokens", sa.BigInteger(), nullable=True),
    sa.Column("price_revision", sa.String(255), nullable=True),
    sa.Column("cost_usd", sa.Numeric(24, 12), nullable=True),
    sa.Column("coverage", JSONB(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revision", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_usage_facts_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_usage_facts_schema_version"),
    sa.Index("ix_execution_usage_facts_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_usage_facts_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("input_tokens >= 0", name="ck_execution_usage_facts_input_tokens"),
    sa.CheckConstraint("output_tokens >= 0", name="ck_execution_usage_facts_output_tokens"),
    sa.CheckConstraint("cost_usd >= 0", name="ck_execution_usage_facts_cost_usd"),
    sa.CheckConstraint("revision >= 0", name="ck_execution_usage_facts_revision"),
    sa.UniqueConstraint("scope_key", "call_identity", name="uq_execution_usage_facts_call"),
    sa.Index("ix_execution_usage_facts_run", "run_id", "purpose", "occurred_at"),
)


def _validate_table(connection: sa.Connection, table: sa.Table) -> None:
    """Compare against PostgreSQL's own canonical rendering of frozen columns/checks.

    Temporary probes avoid fragile whitespace/cast/parenthesis normalization of
    CHECK expressions. The probe has no foreign keys and never contains data.
    """
    from uuid import uuid4

    from sqlalchemy.schema import CreateTable, DropTable

    inspector = sa.inspect(connection)
    probe = table.to_metadata(sa.MetaData(), name=f"ev_validate_{uuid4().hex}")
    probe._prefixes = ["TEMPORARY"]
    connection.execute(CreateTable(probe, include_foreign_key_constraints=[]))
    try:
        canonical = sa.inspect(connection)
        temporary_schema = connection.execute(
            sa.text("SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()")
        ).scalar_one()

        def columns(name, schema=None):
            result = {}
            for column in canonical.get_columns(name, schema=schema):
                result[column["name"]] = (
                    str(column["type"].compile(dialect=connection.dialect)),
                    column["nullable"],
                    column.get("default"),
                    column.get("computed"),
                    column.get("identity"),
                )
            return result

        differences = []
        if columns(table.name) != columns(probe.name, temporary_schema):
            differences.append("columns/types/nullability/defaults/computed")
        for method, key in [("get_pk_constraint", "constrained_columns")]:
            if (
                getattr(inspector, method)(table.name)[key]
                != getattr(canonical, method)(probe.name, schema=temporary_schema)[key]
            ):
                differences.append("primary key")

        def constraints(method, name, schema=None):
            return sorted(
                (
                    item["name"],
                    tuple(item.get("column_names", [])),
                    item.get("sqltext"),
                    item.get("dialect_options", {}),
                )
                for item in getattr(canonical, method)(name, schema=schema)
            )

        differences.extend(
            method
            for method in ("get_unique_constraints", "get_check_constraints")
            if constraints(method, table.name) != constraints(method, probe.name, temporary_schema)
        )
        # Inspector column lists alone do not express NOT VALID or deferred
        # unique/check constraints. PostgreSQL catalogs retain these semantics.
        invalid_constraints = connection.execute(
            sa.text("""
            SELECT count(*) FROM pg_constraint
            WHERE conrelid = CAST(:table AS regclass)
              AND (NOT convalidated OR condeferrable OR condeferred)
        """),
            {"table": table.name},
        ).scalar_one()
        if invalid_constraints:
            differences.append("constraint validation/deferral")
        invalid_indexes = connection.execute(
            sa.text("""
            SELECT count(*) FROM pg_index WHERE indrelid = CAST(:table AS regclass)
              AND (NOT indisvalid OR NOT indisready)
        """),
            {"table": table.name},
        ).scalar_one()
        if invalid_indexes:
            differences.append("index validity")
        expected_fks = sorted(
            (
                c.name,
                tuple(c.column_keys),
                tuple(e.target_fullname for e in c.elements),
                c.ondelete,
                c.onupdate,
                bool(c.deferrable),
                c.initially,
                c.match,
            )
            for c in table.foreign_key_constraints
        )
        if any(
            c.get("referred_schema") not in (None, "public")
            for c in inspector.get_foreign_keys(table.name)
        ):
            differences.append("foreign key schema")
        actual_fks = sorted(
            (
                c["name"],
                tuple(c["constrained_columns"]),
                tuple(f"{c['referred_table']}.{col}" for col in c["referred_columns"]),
                c.get("options", {}).get("ondelete"),
                c.get("options", {}).get("onupdate"),
                c.get("options", {}).get("deferrable", False),
                c.get("options", {}).get("initially"),
                c.get("options", {}).get("match"),
            )
            for c in inspector.get_foreign_keys(table.name)
        )
        if expected_fks != actual_fks:
            differences.append("foreign keys")
        expected_indexes = sorted(
            (i.name, tuple(c.name for c in i.columns), bool(i.unique)) for i in table.indexes
        )
        actual_indexes = []
        for index in inspector.get_indexes(table.name):
            if index.get("duplicates_constraint"):
                continue
            if (
                any(index.get("dialect_options", {}).values())
                or index.get("column_sorting")
                or index.get("expressions")
            ):
                differences.append("index options")
            actual_indexes.append(
                (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            )
        if expected_indexes != sorted(actual_indexes):
            differences.append("indexes")
        if differences:
            raise RuntimeError(
                f"execution view schema mismatch for {table.name}: {', '.join(differences)}"
            )
    finally:
        connection.execute(DropTable(probe))


def upgrade_execution_view(connection: sa.Connection) -> None:
    """Create missing fixed tables; validate all preexisting tables before changes.

    Greenfield imports current ORM metadata, so a fresh installation reaches
    this revision with the tables already present. An old installation creates
    only these tables. Neither path rewrites execution facts.
    """
    from app.infrastructure.security.tenant_rls import policy_statements

    existing = sa.inspect(connection).get_table_names()
    for table in metadata.sorted_tables:
        if table.name in existing:
            _validate_table(connection, table)
    for table in metadata.sorted_tables:
        if table.name not in existing:
            table.create(connection, checkfirst=False)
        for statement in policy_statements(table.name):
            connection.execute(sa.text(statement))
    _install_provenance_scope_guard(connection)
    # ArtifactService is used by API requests and kernel activities. Both roles
    # require provenance INSERT/UPDATE for pending bindings; DELETE is withheld.
    roles = connection.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'), current_setting('app.execution_runtime_role')"
        )
    ).one()
    if not all(roles) or roles[0] == roles[1]:
        raise RuntimeError("distinct API and kernel runtime roles are required")
    quote = connection.dialect.identifier_preparer.quote
    api, kernel = map(quote, roles)
    connection.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_validate_provenance_scope() TO {api}, {kernel}"
        )
    )
    for table in metadata.sorted_tables:
        name = quote(table.name)
        connection.execute(
            sa.text(f"REVOKE ALL PRIVILEGES ON TABLE {name} FROM PUBLIC, {api}, {kernel}")
        )
        connection.execute(sa.text(f"GRANT SELECT ON TABLE {name} TO {api}"))
        grants = (
            "SELECT, INSERT, UPDATE"
            if table.name == "artifact_version_provenance"
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        connection.execute(sa.text(f"GRANT {grants} ON TABLE {name} TO {kernel}"))
        if table.name == "artifact_version_provenance":
            connection.execute(sa.text(f"GRANT INSERT, UPDATE ON TABLE {name} TO {api}"))


def _install_provenance_scope_guard(connection: sa.Connection) -> None:
    """Validate retained identities without granting API access to private facts.

    The definer has table access, but every invocation requires signed claims
    and every source lookup explicitly matches the new row's scope and run.
    Existing FORCE RLS remains in effect for a non-BYPASSRLS migration owner.
    No source payload or row is returned to the caller.
    """
    connection.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION public.opencitadel_validate_provenance_scope()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            producer_steps jsonb := COALESCE(NULLIF(NEW.producer_step_ids, 'null'::jsonb), '[]'::jsonb);
        BEGIN
            IF NOT public.opencitadel_authorization_valid() THEN
                RAISE EXCEPTION 'invalid provenance authorization' USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.artifact_id IS NOT DISTINCT FROM OLD.artifact_id
                   AND NEW.version IS NOT DISTINCT FROM OLD.version
                   AND NEW.producer_identity IS NOT DISTINCT FROM OLD.producer_identity
                   AND NEW.owner_user_id IS NOT DISTINCT FROM OLD.owner_user_id
                   AND NEW.team_id IS NOT DISTINCT FROM OLD.team_id
                   AND NEW.producer_run_id IS NOT DISTINCT FROM OLD.producer_run_id
                   AND NEW.producer_step_ids IS NOT DISTINCT FROM OLD.producer_step_ids
                   AND NEW.activity_id IS NOT DISTINCT FROM OLD.activity_id
                   AND NEW.attempt_id IS NOT DISTINCT FROM OLD.attempt_id
                   AND NEW.invocation_id IS NOT DISTINCT FROM OLD.invocation_id
                   AND NEW.produced_event_id IS NOT DISTINCT FROM OLD.produced_event_id THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.artifacts a
                JOIN public.sessions s ON s.id = a.session_id
                WHERE a.id = NEW.artifact_id AND (
                    (NEW.team_id IS NOT NULL AND s.team_id = NEW.team_id)
                    OR (NEW.team_id IS NULL AND s.team_id IS NULL
                        AND s.owner_user_id = NEW.owner_user_id)
                )
            ) THEN
                RAISE EXCEPTION 'artifact scope mismatch' USING ERRCODE = '23514';
            END IF;
            IF jsonb_typeof(producer_steps) <> 'array' THEN
                RAISE EXCEPTION 'producer reference mismatch' USING ERRCODE = '23514';
            END IF;
            IF NEW.producer_run_id IS NULL AND (
                NEW.activity_id IS NOT NULL OR NEW.attempt_id IS NOT NULL
                OR NEW.invocation_id IS NOT NULL OR NEW.produced_event_id IS NOT NULL
                OR jsonb_array_length(producer_steps) > 0
            ) THEN
                RAISE EXCEPTION 'producer reference mismatch' USING ERRCODE = '23514';
            END IF;
            IF NEW.produced_event_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.execution_events e
                WHERE e.event_id = NEW.produced_event_id AND e.stream_type = 'run'
                  AND e.stream_id = NEW.producer_run_id::text
                  AND e.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id
                  AND e.team_id IS NOT DISTINCT FROM NEW.team_id
            ) THEN
                RAISE EXCEPTION 'producer reference mismatch' USING ERRCODE = '23514';
            END IF;
            IF NEW.activity_id IS NOT NULL AND NOT (
                EXISTS (
                    SELECT 1 FROM public.execution_activity_tasks a
                    WHERE a.activity_id = NEW.activity_id
                      AND a.run_id = NEW.producer_run_id::text
                      AND a.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id
                      AND a.team_id IS NOT DISTINCT FROM NEW.team_id
                ) OR (
                    -- Purged task details can still have a durable kernel projection.
                    NOT EXISTS (SELECT 1 FROM public.execution_activity_tasks a
                                WHERE a.activity_id = NEW.activity_id)
                    AND EXISTS (
                        SELECT 1 FROM public.execution_activity_projection a
                        WHERE a.activity_id = NEW.activity_id AND a.run_id = NEW.producer_run_id
                          AND a.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id
                          AND a.team_id IS NOT DISTINCT FROM NEW.team_id
                    )
                )
            ) THEN
                RAISE EXCEPTION 'producer reference mismatch' USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM jsonb_array_elements(producer_steps) entry
                WHERE jsonb_typeof(entry) <> 'string' OR NOT EXISTS (
                    SELECT 1 FROM public.execution_view_steps s
                    WHERE s.step_id = entry #>> '{}'
                      AND s.run_id = NEW.producer_run_id
                      AND s.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id
                      AND s.team_id IS NOT DISTINCT FROM NEW.team_id
                      AND (NEW.activity_id IS NULL OR s.activity_id = NEW.activity_id)
                )
            ) THEN
                RAISE EXCEPTION 'producer reference mismatch' USING ERRCODE = '23514';
            END IF;
            IF (NEW.attempt_id IS NOT NULL OR NEW.invocation_id IS NOT NULL)
               AND NOT EXISTS (
                SELECT 1 FROM public.execution_view_steps s
                WHERE s.run_id = NEW.producer_run_id
                  AND s.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id
                  AND s.team_id IS NOT DISTINCT FROM NEW.team_id
                  AND (NEW.activity_id IS NULL OR s.activity_id = NEW.activity_id)
                  AND (NEW.attempt_id IS NULL OR s.attempt_id = NEW.attempt_id)
                  AND (NEW.invocation_id IS NULL OR s.invocation_id = NEW.invocation_id)
                  AND (NEW.producer_step_ids IS NULL
                       OR jsonb_array_length(producer_steps) = 0
                       OR producer_steps ? s.step_id)
            ) THEN
                RAISE EXCEPTION 'producer reference mismatch' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    )
    connection.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_validate_provenance_scope() FROM PUBLIC")
    )
    connection.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS artifact_version_provenance_scope ON artifact_version_provenance"
        )
    )
    connection.execute(
        sa.text("""
        CREATE TRIGGER artifact_version_provenance_scope
        BEFORE INSERT OR UPDATE OF artifact_id, version, producer_identity, owner_user_id, team_id,
            producer_run_id, producer_step_ids, activity_id, attempt_id, invocation_id, produced_event_id
        ON artifact_version_provenance FOR EACH ROW
        EXECUTE FUNCTION public.opencitadel_validate_provenance_scope()
    """)
    )
