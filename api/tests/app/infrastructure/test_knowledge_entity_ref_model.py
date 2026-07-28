#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.models.knowledge_base import KnowledgeEntityRefModel


def test_entity_ref_table_shape():
    table = KnowledgeEntityRefModel.__table__
    assert table.name == "knowledge_entity_refs"
    assert set(table.columns.keys()) == {"id", "kb_id", "entity_id", "doc_id", "created_at"}
    fk_targets = {fk.target_fullname for fk in table.foreign_keys}
    assert fk_targets == {
        "knowledge_bases.id",
        "knowledge_entities.id",
        "knowledge_documents.id",
    }
    for fk in table.foreign_keys:
        assert fk.ondelete == "CASCADE"
    uniques = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in c.columns} == {"entity_id", "doc_id"} for c in uniques)


def test_entity_ref_to_domain():
    record = KnowledgeEntityRefModel(id="r1", kb_id="kb1", entity_id="e1", doc_id="d1")
    domain = record.to_domain()
    assert (domain.id, domain.kb_id, domain.entity_id, domain.doc_id) == ("r1", "kb1", "e1", "d1")
