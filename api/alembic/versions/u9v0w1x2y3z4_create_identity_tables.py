"""create identity and audit tables

Revision ID: u9v0w1x2y3z4
Revises: t8u9v0w1x2y3, u8v9w0x1y2z3
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "u9v0w1x2y3z4"
down_revision: Union[str, Sequence[str], None] = ("t8u9v0w1x2y3", "u8v9w0x1y2z3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("avatar_url", sa.String(1024), nullable=False, server_default=sa.text("''")),
        sa.Column("global_role", sa.String(32), nullable=False, server_default=sa.text("'user'")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "teams",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False, server_default=sa.text("''")),
        sa.Column("created_by", sa.String(255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_teams_created_by", "teams", ["created_by"])

    op.create_table(
        "team_members",
        sa.Column("team_id", sa.String(255), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default=sa.text("'member'")),
        sa.Column("joined_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.PrimaryKeyConstraint("team_id", "user_id", name="pk_team_members"),
    )
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])

    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )
    op.create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False, server_default=sa.text("'platform'")),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("team_id", sa.String(255), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("team_role", sa.String(32), nullable=True),
        sa.Column("token", sa.String(255), nullable=False),
        sa.Column("invited_by", sa.String(255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.UniqueConstraint("token", name="uq_invitations_token"),
    )
    op.create_index("ix_invitations_type_email", "invitations", ["type", "email"])
    op.create_index("ix_invitations_team_id", "invitations", ["team_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(1024), nullable=False, server_default=sa.text("''")),
        sa.Column("ip_address", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "user_quotas",
        sa.Column("user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("monthly_token_limit", sa.BigInteger(), nullable=True),
        sa.Column("daily_session_limit", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_tasks", sa.Integer(), nullable=True),
        sa.Column("max_storage_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("actor_user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_ip", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False, server_default=sa.text("''")),
        sa.Column("resource_id", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("team_id", sa.String(255), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    op.create_table(
        "service_api_keys",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("owner_user_id", sa.String(255), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.UniqueConstraint("key_hash", name="uq_service_api_keys_hash"),
    )
    op.create_index("ix_service_api_keys_owner", "service_api_keys", ["owner_user_id"])
    op.create_index("ix_service_api_keys_prefix", "service_api_keys", ["prefix"])


def downgrade() -> None:
    op.drop_index("ix_service_api_keys_prefix", table_name="service_api_keys")
    op.drop_index("ix_service_api_keys_owner", table_name="service_api_keys")
    op.drop_table("service_api_keys")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("user_quotas")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_invitations_team_id", table_name="invitations")
    op.drop_index("ix_invitations_type_email", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_index("ix_teams_created_by", table_name="teams")
    op.drop_table("teams")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_table("users")
