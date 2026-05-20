"""add llm_models skills memory_entries and session columns

Revision ID: a1b2c3d4e5f6
Revises: 0e0d242438bc
Create Date: 2026-05-20 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '0e0d242438bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_models',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column('provider', sa.String(length=64), server_default=sa.text("'openai'"), nullable=False),
        sa.Column('base_url', sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column('api_key', sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column('model_name', sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column('temperature', sa.Float(), server_default=sa.text('0.7'), nullable=False),
        sa.Column('max_tokens', sa.Integer(), server_default=sa.text('8192'), nullable=False),
        sa.Column('extra_params', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'skills',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column('icon', sa.String(length=64), server_default=sa.text("'🤖'"), nullable=False),
        sa.Column('category', sa.String(length=128), server_default=sa.text("'general'"), nullable=False),
        sa.Column('system_prompt', sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column('allowed_tools', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            'recommended_model_id',
            sa.String(length=255),
            sa.ForeignKey('llm_models.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('agent_params', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('examples', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('is_builtin', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_table(
        'memory_entries',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('scope', sa.String(length=32), server_default=sa.text("'global'"), nullable=False),
        sa.Column(
            'session_id',
            sa.String(length=255),
            sa.ForeignKey('sessions.id', ondelete='CASCADE'),
            nullable=True,
        ),
        sa.Column('title', sa.String(length=512), server_default=sa.text("''"), nullable=False),
        sa.Column('content', sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('source', sa.String(length=64), server_default=sa.text("'manual'"), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('use_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column(
        'sessions',
        sa.Column(
            'model_id',
            sa.String(length=255),
            sa.ForeignKey('llm_models.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column(
        'sessions',
        sa.Column(
            'skill_id',
            sa.String(length=255),
            sa.ForeignKey('skills.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('sessions', 'skill_id')
    op.drop_column('sessions', 'model_id')
    op.drop_table('memory_entries')
    op.drop_table('skills')
    op.drop_table('llm_models')
