"""Versioned graph writes and active-version reads."""

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domain.models.knowledge_base import (
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeEntityModel,
    KnowledgeEntityRefModel,
    KnowledgeRelationModel,
)
from app.infrastructure.models.knowledge_version import KnowledgeBaseVersionORM
from app.infrastructure.repositories.kb._shared import _normalize_graph_identity


class KBGraphMixin:
    """Candidate graph write path plus active-version graph reads."""

    async def replace_candidate_graph(
        self,
        kb_id: str,
        version_id: str,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        refs: list[KnowledgeEntityRef],
    ) -> None:
        await self._require_building_candidate(kb_id, version_id)
        rows = [*entities, *relations, *refs]
        if any(row.kb_id != kb_id or row.version_id != version_id for row in rows):
            raise ValueError("candidate graph rows must carry exact version ownership")
        await self._delete_candidate_graph(version_id)
        self.db_session.add_all(
            [
                KnowledgeEntityModel(
                    id=entity.id,
                    kb_id=entity.kb_id,
                    version_id=entity.version_id,
                    name=entity.name,
                    normalized_name=(entity.normalized_name or entity.name.strip().lower()),
                    type=entity.type,
                    description=entity.description,
                )
                for entity in entities
            ]
        )
        await self.db_session.flush()
        self.db_session.add_all(
            [
                KnowledgeRelationModel(
                    id=relation.id,
                    kb_id=relation.kb_id,
                    version_id=relation.version_id,
                    src_entity_id=relation.src_entity_id,
                    dst_entity_id=relation.dst_entity_id,
                    relation=relation.relation,
                    chunk_id=relation.chunk_id,
                )
                for relation in relations
            ]
        )
        self.db_session.add_all(
            [
                KnowledgeEntityRefModel(
                    id=ref.id,
                    kb_id=ref.kb_id,
                    version_id=ref.version_id,
                    entity_id=ref.entity_id,
                    doc_id=ref.doc_id,
                )
                for ref in refs
            ]
        )
        await self.db_session.flush()

    async def upsert_candidate_graph_batch(
        self,
        kb_id: str,
        version_id: str,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        refs: list[KnowledgeEntityRef],
    ) -> None:
        """Merge one extraction batch without a select-then-insert race."""
        if not kb_id.strip() or not version_id.strip():
            raise ValueError("candidate graph identity cannot be empty")
        await self._require_building_candidate(kb_id, version_id)
        rows = [*entities, *relations, *refs]
        if any(row.kb_id != kb_id or row.version_id != version_id for row in rows):
            raise ValueError("candidate graph rows must carry exact version ownership")

        temp_to_persistent: dict[str, str] = {}
        for entity in sorted(
            entities,
            key=lambda item: (
                item.normalized_name,
                item.type,
                item.id,
            ),
        ):
            normalized_name = _normalize_graph_identity(entity.name)
            entity_type = _normalize_graph_identity(entity.type)
            if not normalized_name or not entity_type:
                raise ValueError("candidate entity identity requires normalized name/type")
            if entity.normalized_name != normalized_name:
                raise ValueError("candidate entity normalized_name is not canonical")
            insert_stmt = pg_insert(KnowledgeEntityModel).values(
                id=entity.id,
                kb_id=kb_id,
                version_id=version_id,
                name=entity.name,
                normalized_name=normalized_name,
                type=entity_type,
                description=entity.description,
            )
            statement = insert_stmt.on_conflict_do_update(
                index_elements=[
                    KnowledgeEntityModel.version_id,
                    KnowledgeEntityModel.normalized_name,
                    KnowledgeEntityModel.type,
                ],
                set_={
                    "name": func.least(
                        KnowledgeEntityModel.name,
                        insert_stmt.excluded.name,
                    ),
                    "description": case(
                        (
                            func.length(insert_stmt.excluded.description)
                            > func.length(KnowledgeEntityModel.description),
                            insert_stmt.excluded.description,
                        ),
                        (
                            func.length(insert_stmt.excluded.description)
                            < func.length(KnowledgeEntityModel.description),
                            KnowledgeEntityModel.description,
                        ),
                        else_=func.least(
                            KnowledgeEntityModel.description,
                            insert_stmt.excluded.description,
                        ),
                    ),
                },
            ).returning(KnowledgeEntityModel.id)
            result = await self.db_session.execute(statement)
            persistent_id = result.scalar_one()
            temp_to_persistent[entity.id] = str(persistent_id)

        remapped_relations: list[dict[str, object]] = []
        for relation in relations:
            src_id = temp_to_persistent.get(relation.src_entity_id)
            dst_id = temp_to_persistent.get(relation.dst_entity_id)
            if src_id is None or dst_id is None:
                raise ValueError("candidate relation endpoints are not in its entity batch")
            remapped_relations.append(
                {
                    "id": relation.id,
                    "kb_id": kb_id,
                    "version_id": version_id,
                    "src_entity_id": src_id,
                    "dst_entity_id": dst_id,
                    "relation": relation.relation,
                    "chunk_id": relation.chunk_id,
                }
            )
        if remapped_relations:
            await self.db_session.execute(
                pg_insert(KnowledgeRelationModel)
                .values(remapped_relations)
                .on_conflict_do_nothing(index_elements=[KnowledgeRelationModel.id])
            )

        remapped_refs: list[dict[str, object]] = []
        for ref in refs:
            entity_id = temp_to_persistent.get(ref.entity_id)
            if entity_id is None:
                raise ValueError("candidate entity reference is not in its entity batch")
            remapped_refs.append(
                {
                    "id": ref.id,
                    "kb_id": kb_id,
                    "version_id": version_id,
                    "entity_id": entity_id,
                    "doc_id": ref.doc_id,
                }
            )
        if remapped_refs:
            await self.db_session.execute(
                pg_insert(KnowledgeEntityRefModel)
                .values(remapped_refs)
                .on_conflict_do_nothing(
                    index_elements=[
                        KnowledgeEntityRefModel.entity_id,
                        KnowledgeEntityRefModel.doc_id,
                    ]
                )
            )
        await self.db_session.flush()

    async def _delete_candidate_graph(self, version_id: str) -> None:
        await self.db_session.execute(
            delete(KnowledgeRelationModel).where(KnowledgeRelationModel.version_id == version_id)
        )
        await self.db_session.execute(
            delete(KnowledgeEntityRefModel).where(KnowledgeEntityRefModel.version_id == version_id)
        )
        await self.db_session.execute(
            delete(KnowledgeEntityModel).where(KnowledgeEntityModel.version_id == version_id)
        )

    async def list_entities(self, kb_id: str, name: str | None = None) -> list[KnowledgeEntity]:
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeEntityModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeEntityModel.kb_id == kb_id)
            .where(self._active_version_row_predicate(KnowledgeEntityModel.version_id))
        )
        if name:
            stmt = stmt.where(KnowledgeEntityModel.name.ilike(f"%{name}%"))
        stmt = stmt.order_by(KnowledgeEntityModel.name.asc()).limit(100)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_relations_for_entities(
        self,
        kb_id: str,
        entity_ids: list[str],
    ) -> list[KnowledgeRelation]:
        if not entity_ids:
            return []
        stmt = (
            select(KnowledgeRelationModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeRelationModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(self._active_version_row_predicate(KnowledgeRelationModel.version_id))
            .where(
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                )
            )
            .limit(200)
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_related_chunk_ids(
        self, kb_id: str, chunk_ids: list[str], limit: int = 20
    ) -> list[str]:
        if not chunk_ids:
            return []
        seed_result = await self.db_session.execute(
            select(KnowledgeRelationModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeRelationModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(self._active_version_row_predicate(KnowledgeRelationModel.version_id))
            .where(KnowledgeRelationModel.chunk_id.in_(chunk_ids))
        )
        entity_ids = set()
        for relation in seed_result.scalars().all():
            entity_ids.add(relation.src_entity_id)
            entity_ids.add(relation.dst_entity_id)
        if not entity_ids:
            return []
        related_result = await self.db_session.execute(
            select(KnowledgeRelationModel.chunk_id)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeRelationModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(self._active_version_row_predicate(KnowledgeRelationModel.version_id))
            .where(KnowledgeRelationModel.chunk_id.is_not(None))
            .where(KnowledgeRelationModel.chunk_id.not_in(chunk_ids))
            .where(
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                )
            )
            .limit(max(1, min(limit, 100)))
        )
        return [str(chunk_id) for chunk_id in related_result.scalars().all() if chunk_id]

    async def list_entities_for_version(
        self,
        kb_id: str,
        version_id: str,
        name: str | None = None,
    ) -> list[KnowledgeEntity]:
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseVersionORM,
                (KnowledgeBaseVersionORM.id == KnowledgeEntityModel.version_id)
                & (KnowledgeBaseVersionORM.knowledge_base_id == KnowledgeEntityModel.kb_id),
            )
            .where(
                KnowledgeEntityModel.kb_id == kb_id,
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
        )
        if name:
            stmt = stmt.where(KnowledgeEntityModel.name.ilike(f"%{name}%"))
        stmt = stmt.order_by(
            KnowledgeEntityModel.normalized_name.asc(),
            KnowledgeEntityModel.id.asc(),
        ).limit(100)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_entities_page_for_version(
        self,
        kb_id: str,
        version_id: str,
        *,
        q: str | None,
        after: tuple[str, str] | None,
        limit: int,
    ) -> tuple[list[KnowledgeEntity], tuple[str, str] | None]:
        bounded_limit = max(1, min(limit, 100))
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseVersionORM,
                (KnowledgeBaseVersionORM.id == KnowledgeEntityModel.version_id)
                & (KnowledgeBaseVersionORM.knowledge_base_id == KnowledgeEntityModel.kb_id),
            )
            .where(
                KnowledgeEntityModel.kb_id == kb_id,
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
                KnowledgeEntityModel.normalized_name.is_not(None),
            )
        )
        if q:
            stmt = stmt.where(KnowledgeEntityModel.name.ilike(f"%{q}%"))
        if after is not None:
            stmt = stmt.where(
                or_(
                    KnowledgeEntityModel.normalized_name > after[0],
                    and_(
                        KnowledgeEntityModel.normalized_name == after[0],
                        KnowledgeEntityModel.id > after[1],
                    ),
                )
            )
        result = await self.db_session.execute(
            stmt.order_by(
                KnowledgeEntityModel.normalized_name.asc(),
                KnowledgeEntityModel.id.asc(),
            ).limit(bounded_limit + 1)
        )
        records = list(result.scalars().all())
        has_more = len(records) > bounded_limit
        records = records[:bounded_limit]
        next_key = None
        if has_more and records:
            last = records[-1]
            next_key = (str(last.normalized_name), str(last.id))
        return [record.to_domain() for record in records], next_key

    async def get_entities_by_ids_for_version(
        self,
        kb_id: str,
        version_id: str,
        entity_ids: list[str],
    ) -> list[KnowledgeEntity]:
        if not entity_ids:
            return []
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseVersionORM,
                (KnowledgeBaseVersionORM.id == KnowledgeEntityModel.version_id)
                & (KnowledgeBaseVersionORM.knowledge_base_id == KnowledgeEntityModel.kb_id),
            )
            .where(
                KnowledgeEntityModel.kb_id == kb_id,
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeEntityModel.id.in_(list(dict.fromkeys(entity_ids))),
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
            .order_by(
                KnowledgeEntityModel.normalized_name.asc(),
                KnowledgeEntityModel.id.asc(),
            )
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_relations_for_entities_for_version(
        self,
        kb_id: str,
        version_id: str,
        entity_ids: list[str],
    ) -> list[KnowledgeRelation]:
        if not entity_ids:
            return []
        stmt = (
            select(KnowledgeRelationModel)
            .join(
                KnowledgeBaseVersionORM,
                (KnowledgeBaseVersionORM.id == KnowledgeRelationModel.version_id)
                & (KnowledgeBaseVersionORM.knowledge_base_id == KnowledgeRelationModel.kb_id),
            )
            .where(
                KnowledgeRelationModel.kb_id == kb_id,
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                ),
            )
            .order_by(
                KnowledgeRelationModel.relation.asc(),
                KnowledgeRelationModel.id.asc(),
            )
            .limit(200)
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_related_chunk_ids_for_version(
        self,
        kb_id: str,
        version_id: str,
        chunk_ids: list[str],
        limit: int = 20,
    ) -> list[str]:
        if not chunk_ids:
            return []
        published = (
            select(KnowledgeBaseVersionORM.id)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == kb_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
            .exists()
        )
        seed_result = await self.db_session.execute(
            select(KnowledgeRelationModel)
            .where(
                KnowledgeRelationModel.kb_id == kb_id,
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeRelationModel.chunk_id.in_(chunk_ids),
                published,
            )
            .order_by(KnowledgeRelationModel.id.asc())
        )
        entity_ids: set[str] = set()
        for relation in seed_result.scalars().all():
            entity_ids.add(relation.src_entity_id)
            entity_ids.add(relation.dst_entity_id)
        if not entity_ids:
            return []
        related_result = await self.db_session.execute(
            select(KnowledgeRelationModel.chunk_id)
            .where(
                KnowledgeRelationModel.kb_id == kb_id,
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeRelationModel.chunk_id.is_not(None),
                KnowledgeRelationModel.chunk_id.not_in(chunk_ids),
                published,
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                ),
            )
            .order_by(
                KnowledgeRelationModel.chunk_id.asc(),
                KnowledgeRelationModel.id.asc(),
            )
            .limit(max(1, min(limit, 100)))
        )
        return list(
            dict.fromkeys(str(chunk_id) for chunk_id in related_result.scalars().all() if chunk_id)
        )
