"""drop pruned marketplace and social tables

Revision ID: x2y3z4a5b6c7
Revises: w1x2y3z4a5b6
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op

revision: str = "x2y3z4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "w1x2y3z4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_fortune_predictions_share_id", table_name="marketplace_fortune_predictions")
    op.drop_table("marketplace_fortune_predictions")

    op.drop_index("ix_room_tod_prompts_room", table_name="room_tod_prompts")
    op.drop_table("room_tod_prompts")
    op.drop_index("ix_room_events_room", table_name="room_events")
    op.drop_table("room_events")
    op.drop_index("ix_room_participants_room", table_name="room_participants")
    op.drop_table("room_participants")
    op.drop_index("ix_rooms_code", table_name="rooms")
    op.drop_table("rooms")

    op.drop_index("ix_questionnaire_responses_qid", table_name="questionnaire_responses")
    op.drop_table("questionnaire_responses")
    op.drop_index("ix_questionnaires_slug", table_name="questionnaires")
    op.drop_table("questionnaires")


def downgrade() -> None:
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB

    op.create_table(
        "questionnaires",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("questions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("manage_token", sa.String(64), nullable=False),
        sa.Column("owner_user_id", sa.String(255), nullable=True),
        sa.Column("team_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_questionnaires_slug", "questionnaires", ["slug"], unique=True)

    op.create_table(
        "questionnaire_responses",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("questionnaire_id", sa.String(255), sa.ForeignKey("questionnaires.id", ondelete="CASCADE"), nullable=False),
        sa.Column("answers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("respondent_name", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_questionnaire_responses_qid", "questionnaire_responses", ["questionnaire_id"])

    op.create_table(
        "rooms",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.String(256), nullable=False, server_default=sa.text("''")),
        sa.Column("host_participant_id", sa.String(255), nullable=False),
        sa.Column("tod_mode", sa.String(16), nullable=False, server_default=sa.text("'random'")),
        sa.Column("turn_order", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("current_turn_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_rooms_code", "rooms", ["code"], unique=True)

    op.create_table(
        "room_participants",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("room_id", sa.String(255), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("last_seen", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_room_participants_room", "room_participants", ["room_id"])

    op.create_table(
        "room_events",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("room_id", sa.String(255), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_room_events_room", "room_events", ["room_id"])

    op.create_table(
        "room_tod_prompts",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("room_id", sa.String(255), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
    )
    op.create_index("ix_room_tod_prompts_room", "room_tod_prompts", ["room_id"])

    op.create_table(
        "marketplace_fortune_predictions",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("share_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("input_profile", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("owner_user_id", sa.String(255), nullable=True),
        sa.Column("team_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index(
        "ix_fortune_predictions_share_id",
        "marketplace_fortune_predictions",
        ["share_id"],
        unique=True,
    )
