#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bounded, resumable GraphRAG extraction for immutable KB versions."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, List, Optional

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.knowledge_base import (
    KnowledgeChunk,
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)
_GRAPH_ID_NAMESPACE = uuid.UUID("d03cd8a7-b0dd-45ff-aa12-e40bb0540f77")
_DEFAULT_COMPLETION_TOKEN_ALLOWANCE = 512
_CHECKPOINT_TIMEOUT_SECONDS = 5.0


class _ExtractionFailure(RuntimeError):
    """Extraction failed after a provider result exposed actual usage."""

    def __init__(self, message: str, *, actual_tokens: int) -> None:
        super().__init__(message)
        self.actual_tokens = actual_tokens


GRAPH_EXTRACT_PROMPT = """从以下企业文档片段中抽取对问答有帮助的实体与关系。
只返回 JSON，格式:
{{
  "entities": [{{"name": "...", "type": "组织|产品|流程|制度|人|地点|概念|其他", "description": "..."}}],
  "relations": [{{"src": "实体名", "dst": "实体名", "relation": "关系说明"}}]
}}

文档片段:
{content}
"""


@dataclass(frozen=True)
class GraphBudget:
    """Immutable graph admission and wall-clock limits."""

    max_chunks: int = 10_000
    max_llm_calls: int = 10_000
    max_tokens: int = 1_000_000
    deadline_seconds: float = 300.0

    def __post_init__(self) -> None:
        bounds = {
            "max_chunks": (self.max_chunks, 1, 1_000_000),
            "max_llm_calls": (self.max_llm_calls, 1, 1_000_000),
            "max_tokens": (self.max_tokens, 1, 1_000_000_000),
            "deadline_seconds": (
                self.deadline_seconds,
                0.001,
                3_600.0,
            ),
        }
        for name, (value, minimum, maximum) in bounds.items():
            if not minimum <= value <= maximum:
                raise ValueError(
                    f"{name} must be between {minimum} and {maximum}"
                )


@dataclass(frozen=True)
class GraphBuildResult:
    """Evidence-bearing graph extraction and persistence outcome."""

    attempted: int
    succeeded: int
    failed: int
    invalid: int
    skipped: int
    entity_count: int
    relation_count: int
    persisted: bool
    persistence_error: str | None = None
    warning: str | None = None
    processed: int = 0
    calls: int = 0
    tokens: int = 0
    reserved_tokens: int = 0
    cursor: str | None = None
    budget: GraphBudget | None = None
    degraded_reason: str | None = None

    @property
    def complete(self) -> bool:
        return (
            (self.attempted > 0 or self.cursor is not None)
            and self.succeeded == self.attempted
            and self.failed == 0
            and self.invalid == 0
            and self.skipped == 0
            and self.persisted
            and self.degraded_reason is None
        )

class GraphBuilder:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        llm: Optional[LLM],
        json_parser: JSONParser,
        *,
        max_parent_chunks_per_doc: int = 200,
        concurrency: int = 3,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._json_parser = json_parser
        self._max_parent_chunks_per_doc = max(
            0, max_parent_chunks_per_doc
        )
        self._concurrency = max(1, concurrency)

    async def build(
        self,
        kb_id: str,
        parent_chunks: List[KnowledgeChunk],
        *,
        version_id: str,
        budget: GraphBudget | None = None,
        resume_cursor: str | None = None,
        checkpoint: Callable[[dict[str, Any]], Awaitable[None]]
        | None = None,
        deadline_utc: str | datetime | None = None,
        consumed_chunks: int = 0,
        consumed_llm_calls: int = 0,
        consumed_tokens: int = 0,
        consumed_reserved_tokens: int | None = None,
        consumed_actual_tokens: int | None = None,
    ) -> GraphBuildResult:
        budget = budget or GraphBudget()
        if consumed_reserved_tokens is None:
            consumed_reserved_tokens = consumed_tokens
        if consumed_actual_tokens is None:
            consumed_actual_tokens = consumed_tokens
        for name, value in (
            ("consumed_chunks", consumed_chunks),
            ("consumed_llm_calls", consumed_llm_calls),
            ("consumed_tokens", consumed_tokens),
            ("consumed_reserved_tokens", consumed_reserved_tokens),
            ("consumed_actual_tokens", consumed_actual_tokens),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if not kb_id.strip():
            raise ValueError("candidate graph requires a knowledge base id")
        if not self._llm:
            return GraphBuildResult(
                attempted=0,
                succeeded=0,
                failed=0,
                invalid=0,
                skipped=0,
                entity_count=0,
                relation_count=0,
                persisted=False,
                warning="GraphRAG unavailable: LLM is not configured",
                budget=budget,
                degraded_reason="GRAPH_UNAVAILABLE",
            )
        if not version_id.strip() or any(
            chunk.kb_id != kb_id or chunk.version_id != version_id
            for chunk in parent_chunks
        ):
            raise ValueError(
                "candidate graph requires exact version-scoped chunks"
            )

        async with self._uow_factory() as uow:
            atomic_candidate_upsert = callable(
                getattr(
                    uow.knowledge_base,
                    "upsert_candidate_graph_batch",
                    None,
                )
            )

        selected, capped_count = self._select_chunks(parent_chunks)
        if resume_cursor is not None:
            cursor_key = _decode_cursor(
                resume_cursor,
                kb_id=kb_id,
                version_id=version_id,
            )
            if not any(
                _chunk_sort_key(chunk) == cursor_key
                for chunk in selected
            ):
                raise ValueError(
                    "invalid graph cursor: checkpoint chunk is absent"
                )
            selected = [
                chunk
                for chunk in selected
                if _chunk_sort_key(chunk) > cursor_key
            ]

        deadline_iso, deadline_at = _resolve_deadline(
            deadline_utc,
            budget.deadline_seconds,
        )
        queue: asyncio.Queue[tuple[int, KnowledgeChunk, int] | None] = (
            asyncio.Queue(maxsize=2 * self._concurrency)
        )
        outcomes: asyncio.Queue[
            tuple[
                int,
                KnowledgeChunk,
                int,
                Any,
                int,
                bool,
                Exception | None,
            ]
        ] = asyncio.Queue()

        failed = 0
        invalid = 0
        succeeded = 0
        processed = 0
        calls = 0
        actual_tokens = 0
        reserved_tokens = 0
        cursor: str | None = resume_cursor
        persistence_error: str | None = None
        deadline_stopped = bool(
            selected and deadline_at <= time.monotonic()
        )
        token_stopped = False
        cursor_blocked = False
        persisted_entity_keys: set[tuple[str, str]] = set()
        persisted_relation_ids: set[str] = set()
        aggregate_entities: dict[str, KnowledgeEntity] = {}
        aggregate_relations: dict[str, KnowledgeRelation] = {}
        aggregate_refs: dict[str, KnowledgeEntityRef] = {}
        aggregate_persisted = False
        pending_cursor = cursor
        next_index = 0
        scheduled = 0
        in_flight = 0

        async def durable_checkpoint(
            checkpoint_cursor: str | None,
        ) -> None:
            if checkpoint is None:
                return
            await asyncio.wait_for(
                checkpoint(
                    {
                        "graph_cursor": checkpoint_cursor,
                        "graph_deadline_utc": deadline_iso,
                        "graph_deadline_version_id": version_id,
                        "graph_admitted_chunk_count": calls,
                        "graph_processed_count": processed,
                        "graph_llm_call_count": calls,
                        "graph_token_count": actual_tokens,
                        "graph_actual_token_count": actual_tokens,
                        "graph_reserved_token_count": reserved_tokens,
                        "graph_succeeded_count": succeeded,
                        "graph_failed_count": failed,
                        "graph_invalid_count": invalid,
                    }
                ),
                timeout=_CHECKPOINT_TIMEOUT_SECONDS,
            )

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    index, chunk, reservation = item
                    remaining = deadline_at - time.monotonic()
                    if remaining <= 0:
                        await outcomes.put(
                            (
                                index,
                                chunk,
                                reservation,
                                None,
                                0,
                                False,
                                TimeoutError(
                                    "graph build deadline exceeded"
                                ),
                            )
                        )
                        continue
                    try:
                        payload, used_tokens = await asyncio.wait_for(
                            self._extract_chunk(chunk),
                            timeout=remaining,
                        )
                        await outcomes.put(
                            (
                                index,
                                chunk,
                                reservation,
                                payload,
                                used_tokens,
                                True,
                                None,
                            )
                        )
                    except Exception as exc:
                        used_tokens = (
                            exc.actual_tokens
                            if isinstance(exc, _ExtractionFailure)
                            else 0
                        )
                        logger.warning(
                            "GraphRAG 片段抽取失败 chunk=%s: %s",
                            chunk.id,
                            exc,
                        )
                        await outcomes.put(
                            (
                                index,
                                chunk,
                                reservation,
                                None,
                                used_tokens,
                                True,
                                exc,
                            )
                        )
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(self._concurrency)
        ]
        buffered: dict[
            int,
            tuple[
                KnowledgeChunk,
                Any,
                int,
                bool,
                Exception | None,
                bool,
            ],
        ] = {}

        async def admit_available() -> None:
            nonlocal scheduled, in_flight, calls
            nonlocal reserved_tokens, deadline_stopped
            while (
                scheduled < len(selected)
                and in_flight < self._concurrency
                and (
                    max(consumed_chunks, consumed_llm_calls)
                    + calls
                    < budget.max_chunks
                )
                and (
                    consumed_llm_calls + calls
                    < budget.max_llm_calls
                )
                and not token_stopped
                and not deadline_stopped
            ):
                if deadline_at <= time.monotonic():
                    deadline_stopped = True
                    return
                chunk = selected[scheduled]
                reservation = _worst_case_token_reservation(
                    self._llm,
                    chunk,
                )
                if (
                    consumed_reserved_tokens
                    + reserved_tokens
                    + reservation
                    > budget.max_tokens
                ):
                    return
                index = scheduled
                scheduled += 1
                calls += 1
                reserved_tokens += reservation
                await durable_checkpoint(cursor)
                await queue.put((index, chunk, reservation))
                in_flight += 1

        async def cancel_workers() -> None:
            for task in workers:
                task.cancel()
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    queue.task_done()
            await asyncio.gather(*workers, return_exceptions=True)

        async def finish_workers() -> None:
            for _ in workers:
                await queue.put(None)
            await queue.join()
            await asyncio.gather(*workers, return_exceptions=True)

        try:
            await admit_available()
            while in_flight:
                (
                    index,
                    chunk,
                    reservation,
                    payload,
                    used_tokens,
                    invoked,
                    extraction_error,
                ) = await outcomes.get()
                outcomes.task_done()
                in_flight -= 1
                actual_tokens += used_tokens
                overage = used_tokens > reservation
                if overage:
                    reserved_tokens += used_tokens - reservation
                    token_stopped = True
                if (
                    consumed_reserved_tokens + reserved_tokens
                    > budget.max_tokens
                ):
                    token_stopped = True
                buffered[index] = (
                    chunk,
                    payload,
                    used_tokens,
                    invoked,
                    extraction_error,
                    overage,
                )
                # Outcome usage is durable immediately, independently of the
                # continuous-prefix cursor and ordered persistence.
                await durable_checkpoint(cursor)
                while next_index in buffered:
                    (
                        ordered_chunk,
                        ordered_payload,
                        _ordered_tokens,
                        ordered_invoked,
                        ordered_error,
                        ordered_overage,
                    ) = buffered.pop(next_index)
                    next_index += 1
                    processed += 1
                    next_cursor = cursor
                    outcome_succeeded = False
                    if isinstance(
                        ordered_error,
                        (TimeoutError, asyncio.TimeoutError),
                    ):
                        deadline_stopped = True
                        cursor_blocked = True
                        failed += 1
                    elif ordered_error is not None:
                        cursor_blocked = True
                        failed += 1
                    elif not ordered_invoked:
                        deadline_stopped = True
                        cursor_blocked = True
                        failed += 1
                    elif ordered_overage:
                        cursor_blocked = True
                    elif not _valid_extraction_payload(
                        ordered_payload
                    ):
                        cursor_blocked = True
                        invalid += 1
                    else:
                        entities, relations, refs = (
                            _graph_batch_from_payload(
                                kb_id,
                                version_id,
                                ordered_chunk,
                                ordered_payload,
                            )
                        )
                        if not entities:
                            cursor_blocked = True
                            invalid += 1
                        else:
                            try:
                                if atomic_candidate_upsert:
                                    remaining = (
                                        deadline_at - time.monotonic()
                                    )
                                    if remaining <= 0:
                                        raise TimeoutError(
                                            "graph build deadline exceeded "
                                            "before persistence"
                                        )
                                    await asyncio.wait_for(
                                        self._persist_batch(
                                            kb_id,
                                            version_id,
                                            entities,
                                            relations,
                                            refs,
                                        ),
                                        timeout=remaining,
                                    )
                                else:
                                    _merge_graph_batch(
                                        aggregate_entities,
                                        aggregate_relations,
                                        aggregate_refs,
                                        entities,
                                        relations,
                                        refs,
                                    )
                                succeeded += 1
                                outcome_succeeded = True
                                persisted_entity_keys.update(
                                    (
                                        entity.normalized_name,
                                        entity.type,
                                    )
                                    for entity in entities
                                )
                                persisted_relation_ids.update(
                                    relation.id
                                    for relation in relations
                                )
                                if not cursor_blocked:
                                    next_cursor = _encode_cursor(
                                        kb_id,
                                        version_id,
                                        _chunk_sort_key(
                                            ordered_chunk
                                        ),
                                    )
                            except (
                                TimeoutError,
                                asyncio.TimeoutError,
                            ) as exc:
                                deadline_stopped = True
                                cursor_blocked = True
                                persistence_error = str(exc)
                            except Exception as exc:
                                cursor_blocked = True
                                logger.warning(
                                    "GraphRAG persistence failed: %s",
                                    exc,
                                )
                                persistence_error = str(exc)

                    checkpoint_cursor = cursor
                    if (
                        atomic_candidate_upsert
                        and outcome_succeeded
                        and not cursor_blocked
                    ):
                        checkpoint_cursor = next_cursor
                    elif (
                        not atomic_candidate_upsert
                        and outcome_succeeded
                        and not cursor_blocked
                    ):
                        pending_cursor = next_cursor
                    await durable_checkpoint(checkpoint_cursor)
                    if (
                        atomic_candidate_upsert
                        and checkpoint_cursor != cursor
                    ):
                        cursor = checkpoint_cursor
                await admit_available()
        except BaseException:
            await cancel_workers()
            raise
        else:
            await finish_workers()

        budget_stopped = scheduled < len(selected) or token_stopped

        if not atomic_candidate_upsert:
            try:
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "graph build deadline exceeded before persistence"
                    )
                await asyncio.wait_for(
                    self._persist_aggregate(
                        kb_id,
                        version_id,
                        list(aggregate_entities.values()),
                        list(aggregate_relations.values()),
                        list(aggregate_refs.values()),
                    ),
                    timeout=remaining,
                )
                aggregate_persisted = True
                if not cursor_blocked:
                    await durable_checkpoint(pending_cursor)
                cursor = pending_cursor
            except (TimeoutError, asyncio.TimeoutError) as exc:
                deadline_stopped = True
                cursor_blocked = True
                persistence_error = str(exc)
            except Exception as exc:
                logger.warning("GraphRAG persistence failed: %s", exc)
                persistence_error = str(exc)

        entity_count = len(persisted_entity_keys)
        relation_count = len(persisted_relation_ids)
        if (
            atomic_candidate_upsert
            and persistence_error is None
            and (succeeded or resume_cursor is not None)
        ):
            try:
                async with self._uow_factory() as uow:
                    metrics = (
                        await uow.knowledge_base
                        .get_candidate_index_metrics(kb_id, version_id)
                    )
                entity_count = int(metrics.get("entity_count", 0))
                relation_count = int(metrics.get("relation_count", 0))
            except Exception as exc:
                persistence_error = str(exc)

        persisted = (
            aggregate_persisted
            if not atomic_candidate_upsert
            else (
                (succeeded > 0 or resume_cursor is not None)
                and persistence_error is None
                and entity_count > 0
            )
        )
        degraded_reason: str | None = None
        if budget_stopped or deadline_stopped or capped_count:
            degraded_reason = "GRAPH_PARTIAL"
        elif failed or invalid or persistence_error:
            degraded_reason = "GRAPH_UNAVAILABLE"

        warning_parts: list[str] = []
        if (
            failed
            or invalid
            or capped_count
            or budget_stopped
            or deadline_stopped
        ):
            warning_parts.append(
                f"{degraded_reason or 'GRAPH_INCOMPLETE'} "
                f"attempted={scheduled} succeeded={succeeded} "
                f"failed={failed} invalid={invalid} "
                f"skipped={capped_count}"
            )
        if persistence_error is not None:
            warning_parts.append(
                f"GRAPH_PERSISTENCE_FAILED: {persistence_error}"
            )
        return GraphBuildResult(
            attempted=scheduled,
            succeeded=succeeded,
            failed=failed,
            invalid=invalid,
            skipped=capped_count,
            entity_count=entity_count if persisted else 0,
            relation_count=relation_count if persisted else 0,
            persisted=persisted,
            persistence_error=persistence_error,
            warning="; ".join(warning_parts) or None,
            processed=processed,
            calls=calls,
            tokens=actual_tokens,
            reserved_tokens=reserved_tokens,
            cursor=cursor,
            budget=budget,
            degraded_reason=degraded_reason,
        )

    def _select_chunks(
        self, parent_chunks: list[KnowledgeChunk]
    ) -> tuple[list[KnowledgeChunk], int]:
        from collections import defaultdict

        by_doc: dict[str, list[KnowledgeChunk]] = defaultdict(list)
        for chunk in parent_chunks:
            by_doc[chunk.doc_id].append(chunk)
        selected: list[KnowledgeChunk] = []
        skipped = 0
        for doc_id in sorted(by_doc):
            chunks = sorted(
                by_doc[doc_id],
                key=lambda item: (item.ordinal, item.id),
            )
            capped = chunks[: self._max_parent_chunks_per_doc]
            selected.extend(capped)
            skipped += len(chunks) - len(capped)
        selected.sort(key=_chunk_sort_key)
        return selected, skipped

    async def _persist_batch(
        self,
        kb_id: str,
        version_id: str,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        refs: list[KnowledgeEntityRef],
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.knowledge_base.upsert_candidate_graph_batch(
                kb_id,
                version_id,
                entities,
                relations,
                refs,
            )

    async def _persist_aggregate(
        self,
        kb_id: str,
        version_id: str,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        refs: list[KnowledgeEntityRef],
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.knowledge_base.replace_candidate_graph(
                kb_id,
                version_id,
                entities,
                relations,
                refs,
            )

    async def _extract_chunk(
        self, chunk: KnowledgeChunk
    ) -> tuple[Any, int]:
        prompt = GRAPH_EXTRACT_PROMPT.format(
            content=chunk.content[:6000]
        )
        response = await self._llm.invoke(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        )
        provider_tokens = _provider_total_tokens(response)
        try:
            text_content = _extract_llm_text_content(response)
        except Exception as exc:
            raise _ExtractionFailure(
                str(exc),
                actual_tokens=provider_tokens or 0,
            ) from exc
        if provider_tokens is None:
            provider_tokens = _estimate_total_tokens(
                prompt
            ) + _estimate_total_tokens(text_content)
        try:
            payload = await self._json_parser.invoke(
                text_content,
                default_value={},
            )
        except Exception as exc:
            raise _ExtractionFailure(
                str(exc),
                actual_tokens=provider_tokens,
            ) from exc
        return payload, provider_tokens


def normalize_entity_name(value: str) -> str:
    """NFKC/case/whitespace-stable graph identity component."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.strip().casefold().split())


def normalize_entity_type(value: str) -> str:
    return normalize_entity_name(value)


def _graph_batch_from_payload(
    kb_id: str,
    version_id: str,
    chunk: KnowledgeChunk,
    payload: dict[str, Any],
) -> tuple[
    list[KnowledgeEntity],
    list[KnowledgeRelation],
    list[KnowledgeEntityRef],
]:
    entity_by_key: dict[tuple[str, str], KnowledgeEntity] = {}
    keys_by_name: dict[str, list[tuple[str, str]]] = {}
    for item in payload["entities"]:
        name = str(item["name"]).strip()
        normalized_name = normalize_entity_name(name)
        entity_type = normalize_entity_type(str(item["type"]))
        key = (normalized_name, entity_type)
        entity_by_key.setdefault(
            key,
            KnowledgeEntity(
                id=_stable_id(
                    "entity",
                    version_id,
                    normalized_name,
                    entity_type,
                ),
                kb_id=kb_id,
                version_id=version_id,
                name=name,
                normalized_name=normalized_name,
                type=entity_type,
                description=str(item.get("description") or ""),
            ),
        )
        keys_by_name.setdefault(normalized_name, []).append(key)
    for keys in keys_by_name.values():
        keys.sort()

    relations: list[KnowledgeRelation] = []
    for item in payload["relations"]:
        src_keys = keys_by_name.get(
            normalize_entity_name(str(item["src"])),
            [],
        )
        dst_keys = keys_by_name.get(
            normalize_entity_name(str(item["dst"])),
            [],
        )
        if not src_keys or not dst_keys:
            continue
        src = entity_by_key[src_keys[0]]
        dst = entity_by_key[dst_keys[0]]
        relation_name = str(item["relation"]).strip()
        relations.append(
            KnowledgeRelation(
                id=_stable_id(
                    "relation",
                    version_id,
                    src.normalized_name,
                    src.type,
                    dst.normalized_name,
                    dst.type,
                    relation_name,
                    chunk.id,
                ),
                kb_id=kb_id,
                version_id=version_id,
                src_entity_id=src.id,
                dst_entity_id=dst.id,
                relation=relation_name,
                chunk_id=chunk.id,
            )
        )
    refs = [
        KnowledgeEntityRef(
            id=_stable_id(
                "entity-ref",
                version_id,
                entity.normalized_name,
                entity.type,
                chunk.doc_id,
            ),
            kb_id=kb_id,
            version_id=version_id,
            entity_id=entity.id,
            doc_id=chunk.doc_id,
        )
        for entity in sorted(
            entity_by_key.values(),
            key=lambda item: (item.normalized_name, item.type),
        )
    ]
    return list(entity_by_key.values()), relations, refs


def _merge_graph_batch(
    aggregate_entities: dict[str, KnowledgeEntity],
    aggregate_relations: dict[str, KnowledgeRelation],
    aggregate_refs: dict[str, KnowledgeEntityRef],
    entities: list[KnowledgeEntity],
    relations: list[KnowledgeRelation],
    refs: list[KnowledgeEntityRef],
) -> None:
    for entity in entities:
        current = aggregate_entities.get(entity.id)
        if current is None:
            aggregate_entities[entity.id] = entity
            continue
        descriptions = (current.description or "", entity.description or "")
        description = min(
            descriptions,
            key=lambda value: (-len(value), value),
        )
        aggregate_entities[entity.id] = current.model_copy(
            update={
                "name": min(current.name, entity.name),
                "description": description,
            }
        )
    for relation in relations:
        aggregate_relations.setdefault(relation.id, relation)
    for ref in refs:
        aggregate_refs.setdefault(ref.id, ref)


def _stable_id(kind: str, *parts: str) -> str:
    return str(
        uuid.uuid5(
            _GRAPH_ID_NAMESPACE,
            "\x1f".join((kind, *parts)),
        )
    )


def _chunk_sort_key(chunk: KnowledgeChunk) -> tuple[str, int, str]:
    return chunk.doc_id, int(chunk.ordinal), chunk.id


def _encode_cursor(
    kb_id: str,
    version_id: str,
    key: tuple[str, int, str],
) -> str:
    raw = json.dumps(
        {
            "kb": kb_id,
            "version": version_id,
            "doc": key[0],
            "ordinal": key[1],
            "id": key[2],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(
    value: str,
    *,
    kb_id: str,
    version_id: str,
) -> tuple[str, int, str]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(value + padding).decode()
        )
        if (
            payload.get("kb") != kb_id
            or payload.get("version") != version_id
        ):
            raise ValueError
        document_id = payload["doc"]
        ordinal = payload["ordinal"]
        chunk_id = payload["id"]
        if (
            not isinstance(document_id, str)
            or not document_id
            or not isinstance(ordinal, int)
            or ordinal < 0
            or not isinstance(chunk_id, str)
            or not chunk_id
        ):
            raise ValueError
        return document_id, ordinal, chunk_id
    except Exception as exc:
        raise ValueError("invalid graph cursor") from exc


def _estimate_total_tokens(content: str) -> int:
    # Deterministic fallback: approximately four codepoints per token.
    return max(1, (len(content) + 3) // 4)


def _worst_case_token_reservation(
    llm: LLM,
    chunk: KnowledgeChunk,
) -> int:
    prompt = GRAPH_EXTRACT_PROMPT.format(
        content=chunk.content[:6000]
    )
    try:
        configured = int(getattr(llm, "max_tokens"))
    except (AttributeError, TypeError, ValueError):
        configured = _DEFAULT_COMPLETION_TOKEN_ALLOWANCE
    if configured <= 0:
        configured = _DEFAULT_COMPLETION_TOKEN_ALLOWANCE
    completion_allowance = max(1, configured)
    # One token per UTF-8 byte is deliberately conservative for admission.
    # The /4 estimator remains only the missing-provider-usage fallback.
    return len(prompt.encode("utf-8")) + completion_allowance


def _resolve_deadline(
    value: str | datetime | None,
    deadline_seconds: float,
) -> tuple[str, float]:
    now_utc = datetime.now(timezone.utc)
    if value is None:
        deadline = now_utc + timedelta(seconds=deadline_seconds)
    else:
        try:
            deadline = (
                value
                if isinstance(value, datetime)
                else datetime.fromisoformat(value)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid graph deadline") from exc
        if deadline.tzinfo is None:
            raise ValueError("graph deadline must be timezone-aware")
        deadline = deadline.astimezone(timezone.utc)
    remaining = max(0.0, (deadline - now_utc).total_seconds())
    return deadline.isoformat(), time.monotonic() + remaining


def _provider_total_tokens(response: Any) -> int | None:
    if not isinstance(response, dict):
        return None
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("total_tokens")
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _valid_extraction_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    entities = payload.get("entities")
    relations = payload.get("relations")
    if not isinstance(entities, list) or not isinstance(relations, list):
        return False
    if not entities:
        return False

    entity_names: set[str] = set()
    for item in entities:
        if not isinstance(item, dict):
            return False
        if {"name", "type", "description"}.difference(item):
            return False
        name = item["name"]
        entity_type = item["type"]
        description = item["description"]
        if (
            not isinstance(name, str)
            or not normalize_entity_name(name)
            or not isinstance(entity_type, str)
            or not normalize_entity_type(entity_type)
            or not isinstance(description, str)
        ):
            return False
        entity_names.add(normalize_entity_name(name))

    for item in relations:
        if not isinstance(item, dict):
            return False
        if {"src", "dst", "relation"}.difference(item):
            return False
        src = item["src"]
        dst = item["dst"]
        relation = item["relation"]
        if (
            not isinstance(src, str)
            or not normalize_entity_name(src)
            or not isinstance(dst, str)
            or not normalize_entity_name(dst)
            or not isinstance(relation, str)
            or not relation.strip()
            or normalize_entity_name(src) not in entity_names
            or normalize_entity_name(dst) not in entity_names
        ):
            return False
    return True


def _extract_llm_text_content(response: dict) -> str:
    content = response.get("content") or ""
    if isinstance(content, str) and content.strip():
        return content
    reasoning = response.get("reasoning_content") or ""
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    return "{}"
