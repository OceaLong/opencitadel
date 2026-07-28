"""create knowledge_entity_refs

Revision ID: a5b6c7d8e9f0
Revises: kk06audithard
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "kk06audithard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_entity_refs",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(length=255),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(length=255),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_id",
            sa.String(length=255),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.UniqueConstraint("entity_id", "doc_id", name="uq_kb_entity_refs_entity_doc"),
    )
    op.create_index("ix_kb_entity_refs_doc", "knowledge_entity_refs", ["doc_id"])
    op.create_index("ix_kb_entity_refs_entity", "knowledge_entity_refs", ["entity_id"])
    # 存量回填：由 relations.chunk_id -> chunks.doc_id 反推实体来源文档。
    # id 用 md5(entity_id:doc_id) 保证确定性；孤儿实体（无任何关系）无法反推，保守不回填。
    op.execute(
        """
        INSERT INTO knowledge_entity_refs (id, kb_id, entity_id, doc_id)
        SELECT md5(x.entity_id || ':' || x.doc_id), x.kb_id, x.entity_id, x.doc_id
        FROM (
            SELECT DISTINCT r.kb_id, r.src_entity_id AS entity_id, c.doc_id
            FROM knowledge_relations r
            JOIN knowledge_chunks c ON c.id = r.chunk_id
            UNION
            SELECT DISTINCT r.kb_id, r.dst_entity_id AS entity_id, c.doc_id
            FROM knowledge_relations r
            JOIN knowledge_chunks c ON c.id = r.chunk_id
        ) x
        ON CONFLICT (entity_id, doc_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_kb_entity_refs_entity", table_name="knowledge_entity_refs")
    op.drop_index("ix_kb_entity_refs_doc", table_name="knowledge_entity_refs")
    op.drop_table("knowledge_entity_refs")
