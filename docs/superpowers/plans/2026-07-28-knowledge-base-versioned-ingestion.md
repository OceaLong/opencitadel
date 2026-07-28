# Knowledge Base Versioned Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make document ingestion versioned and atomically published, keep core retrieval available during rebuilds and vector failures, and expose truthful citations, graph data, pagination, and Ask/Agent entry points.

**Architecture:** Each logical knowledge base owns immutable `knowledge_base_versions`; a build creates a candidate manifest of immutable document revisions and writes all candidate index/graph rows under its `version_id`. The build publishes by switching `knowledge_bases.active_version_id` in a short transaction, while sessions continue querying their bound version through the shared governance interfaces.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, PostgreSQL/pgvector, Pydantic v2, Redis notifications with PostgreSQL event replay, pytest, Next.js/React/TypeScript, Vitest.

## Global Constraints

- Requires completion of `2026-07-28-agent-and-resource-governance.md` and Alembic head `b6c7d8e9f0a1`.
- A document is not queryable merely because parsing succeeded; only an indexed document revision in a published version is queryable.
- Parsing, chunking, and keyword indexing are mandatory for publish.
- Vector failure publishes a visible degraded version with BM25 fallback; graph failure does not block core retrieval.
- A failed candidate never changes or clears the active version.
- Every retrieval result and source read includes `version_id` and immutable document revision identity.
- Existing API fields remain available for one compatibility release and project to the active version.
- Existing worktree changes must not be included in task commits.

---

## File Structure

### New files

- `api/app/domain/models/knowledge_version.py` — version, document revision, manifest, capability, and graph DTOs.
- `api/app/domain/repositories/knowledge_version_repository.py` — version-scoped persistence contract.
- `api/app/domain/services/knowledge_base/version_builder.py` — candidate creation, manifest assembly, validation, and publish.
- `api/app/application/services/knowledge_version_service.py` — version listing, resolution, upgrade metadata, retry/cancel.
- `api/app/infrastructure/models/knowledge_version.py` — SQLAlchemy version/revision/manifest models.
- `api/app/infrastructure/repositories/db_knowledge_version_repository.py` — PostgreSQL implementation.
- `api/alembic/versions/c7d8e9f0a1b2_add_knowledge_base_versions.py` — version schema and v1 backfill.
- `api/tests/app/contracts/test_kb_version_invariants.py` — failure injection and history-reproducibility suite.
- `ui/src/components/knowledge/knowledge-version-status.tsx` — active/candidate/degraded status.
- `ui/src/components/knowledge/knowledge-graph.tsx` — real entity/relation graph.
- `ui/src/components/knowledge/document-pager.tsx` — cursor-based source preview.

### Existing files with focused modifications

- `api/app/infrastructure/models/knowledge_base.py` — active version and version-scoped index columns.
- `api/app/domain/repositories/{knowledge_base_repository,uow}.py` and DB implementations — expose version repository.
- `api/app/domain/services/knowledge_base/{ingestion_runner,ingestion_task_runner,chunker,retriever,graph_builder}.py` — candidate version pipeline.
- `api/app/application/services/knowledge_base_service.py` — commands create builds rather than mutate active data.
- `api/app/domain/services/tools/knowledge_base_tools.py` — binding-aware retrieval/source/graph tools.
- `api/app/interfaces/schemas/knowledge_base.py` and `api/app/interfaces/endpoints/knowledge_base_routes.py` — version/build/graph/page contracts.
- `api/app/application/services/task_runner_factory.py` — construct KnowledgeBaseTool with bound version.
- `api/app/worker/main.py` — version build reconciliation.
- `ui/src/lib/api/{knowledge,types}.ts` — versioned API types.
- `ui/src/components/knowledge/knowledge-library.tsx` — dual entry and build status.
- `ui/src/components/workspace/knowledge-context-panel.tsx` — real graph and pagination.

## Task 1: Add Knowledge Version Schema and Backfill Existing Data

**Files:**

- Create: `api/app/domain/models/knowledge_version.py`
- Create: `api/app/infrastructure/models/knowledge_version.py`
- Create: `api/alembic/versions/c7d8e9f0a1b2_add_knowledge_base_versions.py`
- Modify: `api/app/infrastructure/models/knowledge_base.py`
- Modify: `api/app/infrastructure/models/__init__.py`
- Test: `api/tests/app/alembic/test_knowledge_version_migration.py`

**Interfaces:**

- Consumes: `ResourceBuild` and `BuildState` from the shared plan.
- Produces: `KnowledgeBaseVersion`, `KnowledgeDocumentRevision`, `KnowledgeVersionDocument`.
- Produces: `knowledge_bases.active_version_id`.

- [ ] **Step 1: Write the migration/backfill test**

```python
def test_existing_kb_is_backfilled_as_published_v1(migrated_db, legacy_kb):
    row = migrated_db.fetch_one(
        "SELECT active_version_id FROM knowledge_bases WHERE id = :id",
        {"id": legacy_kb.id},
    )
    version = migrated_db.fetch_one(
        "SELECT state, legacy_snapshot FROM knowledge_base_versions WHERE id = :id",
        {"id": row.active_version_id},
    )
    assert version.state in {"ready", "degraded"}
    assert version.legacy_snapshot is True
    assert migrated_db.scalar(
        "SELECT count(*) FROM knowledge_chunks WHERE kb_id=:id AND version_id IS NULL",
        {"id": legacy_kb.id},
    ) == 0
    assert migrated_db.scalar(
        """
        SELECT count(*) FROM session_resource_bindings
        WHERE session_id=:session_id
          AND resource_kind='knowledge_base'
          AND version_id=:version_id
          AND is_current=true
        """,
        {"session_id": legacy_kb.session_id, "version_id": row.active_version_id},
    ) == 1
```

- [ ] **Step 2: Run the migration test**

Run: `cd api && .venv/bin/pytest tests/app/alembic/test_knowledge_version_migration.py -q`

Expected: FAIL because version tables/columns do not exist.

- [ ] **Step 3: Create the schema with revision `c7d8e9f0a1b2`**

Create:

```text
knowledge_base_versions(
  id, knowledge_base_id, parent_version_id, build_id, state,
  capabilities jsonb, degraded_reasons jsonb, metrics jsonb,
  legacy_snapshot boolean, created_at, published_at
)
knowledge_document_revisions(
  id, document_id, source_ref, source_digest, parsed_blocks jsonb,
  page_count, state, error, warning, created_at
)
knowledge_base_version_documents(
  version_id, document_id, document_revision_id, ordinal, state, error, warning
)
```

Add non-null `version_id` after backfill to chunks, entities, relations, and entity refs. Add `active_version_id` to knowledge bases. Backfill one deterministic version ID per existing KB and one revision per existing document; mark backfilled versions `legacy_snapshot=true`. Use `down_revision = "b6c7d8e9f0a1"`.

Add `normalized_name` to entities and create unique graph identity `(version_id, normalized_name, type)`. Add indexes for `(version_id, doc_id)`, vector search by version, graph lookups by version, and a unique manifest key `(version_id, document_id)`. Backfill a current `session_resource_bindings` row for every legacy session with `knowledge_base_id`; retain the legacy session column during the compatibility window.

- [ ] **Step 4: Apply and reverse the migration in a disposable database**

Run: `cd api && .venv/bin/alembic upgrade c7d8e9f0a1b2`

Run: `cd api && .venv/bin/pytest tests/app/alembic/test_knowledge_version_migration.py -q`

Run: `cd api && .venv/bin/alembic downgrade b6c7d8e9f0a1 && .venv/bin/alembic upgrade c7d8e9f0a1b2`

Expected: both directions succeed and the backfill test PASSes after the final upgrade.

- [ ] **Step 5: Commit the version schema**

```bash
git add api/app/domain/models/knowledge_version.py api/app/infrastructure/models/knowledge_version.py api/app/infrastructure/models/knowledge_base.py api/app/infrastructure/models/__init__.py api/alembic/versions/c7d8e9f0a1b2_add_knowledge_base_versions.py api/tests/app/alembic/test_knowledge_version_migration.py
git commit -m "feat(knowledge): add immutable knowledge base versions"
```

## Task 2: Implement the Version Repository and ResourceVersionProvider

**Files:**

- Create: `api/app/domain/repositories/knowledge_version_repository.py`
- Create: `api/app/infrastructure/repositories/db_knowledge_version_repository.py`
- Create: `api/app/application/services/knowledge_version_service.py`
- Modify: `api/app/domain/repositories/uow.py`
- Modify: `api/app/infrastructure/repositories/db_uow.py`
- Modify: `api/app/domain/repositories/knowledge_base_repository.py`
- Modify: `api/app/infrastructure/repositories/db_knowledge_base_repository.py`
- Test: `api/tests/app/infrastructure/repositories/test_db_knowledge_version_repository.py`
- Test: `api/tests/app/application/services/test_knowledge_version_service.py`

**Interfaces:**

- Produces: `KnowledgeVersionService.resolve_published_version(...) -> PublishedResourceVersion`.
- Produces repository methods `create_candidate`, `get_version`, `list_versions`, `get_manifest`, `publish_candidate`, `fail_candidate`.
- Consumes: `ResourceVersionProvider` from the shared plan.

- [ ] **Step 1: Write repository and provider tests**

```python
@pytest.mark.asyncio
async def test_provider_defaults_to_active_version(service, ready_kb, scope):
    resolved = await service.resolve_published_version(ready_kb.id, None, scope)
    assert resolved.version_id == ready_kb.active_version_id


@pytest.mark.asyncio
async def test_provider_rejects_building_or_foreign_version(service, scope):
    with pytest.raises(BadRequestError):
        await service.resolve_published_version("kb1", "building-v2", scope)
    with pytest.raises(NotFoundError):
        await service.resolve_published_version("kb1", "other-kb-v1", scope)


@pytest.mark.asyncio
async def test_publish_compare_and_swaps_parent(repo, candidate):
    assert await repo.publish_candidate(candidate.id, expected_active_version_id=candidate.parent_version_id)
    assert not await repo.publish_candidate(candidate.id, expected_active_version_id=candidate.parent_version_id)
```

- [ ] **Step 2: Run provider/repository tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/repositories/test_db_knowledge_version_repository.py tests/app/application/services/test_knowledge_version_service.py -q`

Expected: FAIL because version repository is absent.

- [ ] **Step 3: Implement scope-aware resolution and compare-and-swap publish**

Use:

```python
async def publish_candidate(
    self,
    version_id: str,
    *,
    expected_active_version_id: str | None,
    state: KnowledgeVersionState,
    capabilities: dict[str, bool],
    degraded_reasons: list[str],
    metrics: dict[str, int | float],
) -> bool:
    ...
```

The DB implementation locks the logical KB row, verifies its current active ID equals `expected_active_version_id`, updates candidate state/published time, and switches active ID in one transaction. A mismatch leaves both versions untouched and returns false.

- [ ] **Step 4: Run version and UoW tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/repositories/test_db_knowledge_version_repository.py tests/app/application/services/test_knowledge_version_service.py tests/app/infrastructure/repositories/test_db_uow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit version persistence**

```bash
git add api/app/domain/repositories/knowledge_version_repository.py api/app/infrastructure/repositories/db_knowledge_version_repository.py api/app/application/services/knowledge_version_service.py api/app/domain/repositories/uow.py api/app/infrastructure/repositories/db_uow.py api/app/domain/repositories/knowledge_base_repository.py api/app/infrastructure/repositories/db_knowledge_base_repository.py api/tests/app/infrastructure/repositories/test_db_knowledge_version_repository.py api/tests/app/application/services/test_knowledge_version_service.py
git commit -m "feat(knowledge): persist and resolve knowledge versions"
```

## Task 3: Build Candidate Manifests from Immutable Document Revisions

**Files:**

- Create: `api/app/domain/services/knowledge_base/version_builder.py`
- Modify: `api/app/application/services/knowledge_base_service.py:88-221,298-342`
- Modify: `api/app/domain/models/knowledge_base.py`
- Test: `api/tests/app/domain/services/knowledge_base/test_version_builder.py`
- Test: `api/tests/app/application/services/test_knowledge_base_build_commands.py`

**Interfaces:**

- Consumes: Task 2 repository and shared ResourceBuildService.
- Produces: `KnowledgeVersionBuilder.create_candidate(command)`.
- Produces: `KnowledgeBuildCommand` with `add`, `remove`, `replace`, or `reindex`.

- [ ] **Step 1: Write manifest copy-on-write tests**

```python
@pytest.mark.asyncio
async def test_add_document_reuses_unchanged_revisions(builder, active_v1):
    candidate = await builder.create_candidate(
        KnowledgeBuildCommand.add("kb1", [uploaded_file("f3")])
    )
    manifest = await builder.manifest(candidate.version_id)
    assert revision_ids(manifest)[:2] == revision_ids(active_v1.manifest)
    assert manifest[2].revision.state == DocumentRevisionState.UPLOADED


@pytest.mark.asyncio
async def test_remove_document_does_not_delete_old_version(builder, active_v1):
    candidate = await builder.create_candidate(KnowledgeBuildCommand.remove("kb1", "doc2"))
    assert "doc2" not in document_ids(await builder.manifest(candidate.version_id))
    assert "doc2" in document_ids(await builder.manifest(active_v1.id))
```

- [ ] **Step 2: Run builder command tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_version_builder.py tests/app/application/services/test_knowledge_base_build_commands.py -q`

Expected: FAIL because current service mutates documents and active index directly.

- [ ] **Step 3: Implement immutable revision and manifest commands**

`add_documents()` validates source ownership/public URLs, creates revisions, creates a candidate manifest from the active version plus additions, and registers one build. `delete_document()` creates a candidate manifest excluding the document; it does not delete old rows. `reindex()` creates new revisions only when source digest changed, otherwise reuses revisions and rebuilds candidate index rows.

Use a partial unique constraint in shared `resource_builds` so one KB has at most one queued/running build. Duplicate commands return the existing build ID and candidate version.

- [ ] **Step 4: Run command, ownership, URL guard, and delete tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_version_builder.py tests/app/application/services/test_knowledge_base_build_commands.py tests/app/domain/services/knowledge_base/test_url_guard.py tests/app/application/services/test_knowledge_base_service.py -q`

Expected: PASS; old manifest remains queryable after add/remove commands.

- [ ] **Step 5: Commit candidate manifest commands**

```bash
git add api/app/domain/services/knowledge_base/version_builder.py api/app/application/services/knowledge_base_service.py api/app/domain/models/knowledge_base.py api/tests/app/domain/services/knowledge_base/test_version_builder.py api/tests/app/application/services/test_knowledge_base_build_commands.py
git commit -m "feat(knowledge): build immutable document manifests"
```

## Task 4: Convert KBIngestionRunner to Staged, Atomic Publication

**Files:**

- Modify: `api/app/domain/services/knowledge_base/ingestion_runner.py`
- Modify: `api/app/domain/services/knowledge_base/ingestion_task_runner.py`
- Modify: `api/app/domain/services/knowledge_base/chunker.py`
- Modify: `api/app/worker/main.py:327-371,626-667`
- Test: `api/tests/app/domain/services/knowledge_base/test_versioned_ingestion_runner.py`
- Test: `api/tests/app/worker/test_kb_build_reconciliation.py`

**Interfaces:**

- Consumes: candidate version/build from Task 3.
- Produces: build phases `parse`, `chunk`, `keyword_index`, `vector_index`, `graph`, `validate`, `publish`.
- Produces: published capabilities `keyword_search`, `vector_search`, `graph_search`.

- [ ] **Step 1: Write stage-failure and document-state tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("failing_phase", ["parse", "chunk", "keyword_index", "validate", "publish"])
async def test_core_failure_keeps_active_version(runner, kb_with_v1, failing_phase):
    events = await collect(runner.run(build_for(kb_with_v1, fail_at=failing_phase)))
    assert await active_version_id(kb_with_v1.id) == kb_with_v1.v1.id
    assert await search_version(kb_with_v1.v1.id, "known") == ["known result"]
    assert terminal_build_state(events) == BuildState.FAILED


@pytest.mark.asyncio
async def test_parsed_revision_is_not_queryable_before_publish(runner, candidate):
    await runner.stop_after(candidate.build_id, phase="parse")
    assert await revision_state(candidate.new_revision_id) == DocumentRevisionState.PARSED
    assert await search_version(candidate.version_id, "text") == []


@pytest.mark.asyncio
async def test_one_document_failure_publishes_partial_warning(runner, candidate_with_two_documents):
    outcome = await collect(
        runner.run(candidate_with_two_documents.build_id, fail_document_id="doc-bad")
    )
    assert terminal_build_state(outcome) == BuildState.DEGRADED
    assert "DOCUMENT_PARTIAL" in await version_degraded_reasons(
        candidate_with_two_documents.version_id
    )
    assert await active_version_id(candidate_with_two_documents.kb_id) == (
        candidate_with_two_documents.version_id
    )
```

- [ ] **Step 2: Run versioned runner tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_versioned_ingestion_runner.py tests/app/worker/test_kb_build_reconciliation.py -q`

Expected: FAIL because Runner accepts `kb_id`, marks READY after parse, and writes active rows.

- [ ] **Step 3: Make the Runner build a candidate version**

Change entry to:

```python
async def run(self, build_id: str) -> AsyncGenerator[BaseEvent, None]:
    build = await build_service.require_running_build(build_id, ResourceKind.KNOWLEDGE_BASE)
    version = await version_repo.get_version(build.version_id)
    ...
```

Persist revision transitions `uploaded→parsing→parsed→indexing→indexed|failed`. A whole-phase failure, zero successfully parsed documents, chunk failure, keyword-index failure, validation failure, or publish failure fails the candidate. An individual document failure is isolated in its manifest row; when at least one document completes the mandatory core, publish as degraded with `DOCUMENT_PARTIAL`. Write chunks with candidate `version_id`; keyword index completion is mandatory. Vector failures append `EMBEDDING_UNAVAILABLE` and continue. Graph failures append `GRAPH_UNAVAILABLE` and continue. Validate counts and referential closure, then compare-and-swap publish. Never call `clear_index_data()` or purge rows belonging to active/older versions.

The Worker reconciliation queries shared stale builds for kind `knowledge_base`; an expired build is failed without changing active version.

- [ ] **Step 4: Run runner, reconciliation, and legacy ingestion tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_versioned_ingestion_runner.py tests/app/domain/services/knowledge_base/test_kb_ingestion_runner.py tests/app/worker/test_kb_build_reconciliation.py -q`

Expected: PASS; update legacy tests to assert candidate publication rather than direct KB status mutation.

- [ ] **Step 5: Commit atomic ingestion**

```bash
git add api/app/domain/services/knowledge_base/ingestion_runner.py api/app/domain/services/knowledge_base/ingestion_task_runner.py api/app/domain/services/knowledge_base/chunker.py api/app/worker/main.py api/tests/app/domain/services/knowledge_base/test_versioned_ingestion_runner.py api/tests/app/domain/services/knowledge_base/test_kb_ingestion_runner.py api/tests/app/worker/test_kb_build_reconciliation.py
git commit -m "feat(knowledge): publish staged knowledge indexes atomically"
```

## Task 5: Make Retrieval and Citations Version-Aware with BM25 Fallback

**Files:**

- Modify: `api/app/domain/services/knowledge_base/retriever.py`
- Modify: `api/app/domain/services/tools/knowledge_base_tools.py`
- Modify: `api/app/infrastructure/repositories/db_knowledge_base_repository.py`
- Modify: `api/app/application/services/task_runner_factory.py:407-425`
- Modify: `api/app/domain/models/event.py`
- Modify: `api/app/interfaces/schemas/event.py`
- Test: `api/tests/app/domain/services/knowledge_base/test_versioned_retriever.py`
- Test: `api/tests/app/domain/services/tools/test_knowledge_base_tools.py`

**Interfaces:**

- Consumes: current session binding version ID.
- Produces: `KnowledgeCitation(version_id, document_revision_id, doc_id, page_no, chunk_id)`.
- Produces: `HybridRetriever.retrieve(kb_id, version_id, query, limit)`.

- [ ] **Step 1: Write version isolation and fallback tests**

```python
@pytest.mark.asyncio
async def test_retrieval_never_crosses_bound_version(retriever):
    results = await retriever.retrieve("kb1", "kbv1", "release policy", limit=10)
    assert {r.version_id for r in results} == {"kbv1"}
    assert "v2 only" not in [r.content for r in results]


@pytest.mark.asyncio
async def test_embedding_failure_returns_bm25_with_degraded_metadata(retriever):
    retriever.vector.embed.side_effect = TimeoutError()
    response = await retriever.retrieve("kb1", "kbv2", "关键词", limit=5)
    assert response.items
    assert response.capabilities["vector_search"] is False
    assert response.degraded_reasons == ["EMBEDDING_UNAVAILABLE"]
```

- [ ] **Step 2: Run retriever/tool tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_versioned_retriever.py tests/app/domain/services/tools/test_knowledge_base_tools.py -q`

Expected: FAIL because queries filter only `kb_id` and citations lack version/revision.

- [ ] **Step 3: Thread version_id through every query and citation**

All vector, BM25, parent expansion, rerank, graph expansion, entity, relation, and source reads require both `kb_id` and `version_id`. Construct KnowledgeBaseTool with immutable `version_id`. Add citation fields to message/tool presentation while retaining existing `kbdoc://<doc_id>?page=<n>` links during compatibility; add `version=<id>&revision=<id>`.

- [ ] **Step 4: Run retriever, tool, event schema, and Ask-flow tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_versioned_retriever.py tests/app/domain/services/knowledge_base/test_retriever.py tests/app/domain/services/tools/test_knowledge_base_tools.py tests/app/domain/services/flows/test_ask_flows.py tests/app/interfaces/schemas/test_event_mapper.py -q`

Expected: PASS; every result contains the requested version.

- [ ] **Step 5: Commit versioned retrieval**

```bash
git add api/app/domain/services/knowledge_base/retriever.py api/app/domain/services/tools/knowledge_base_tools.py api/app/infrastructure/repositories/db_knowledge_base_repository.py api/app/application/services/task_runner_factory.py api/app/domain/models/event.py api/app/interfaces/schemas/event.py api/tests/app/domain/services/knowledge_base api/tests/app/domain/services/tools/test_knowledge_base_tools.py api/tests/app/interfaces/schemas/test_event_mapper.py
git commit -m "feat(knowledge): bind retrieval and citations to versions"
```

## Task 6: Make GraphRAG Versioned, Race-Safe, Bounded, and Truthful

**Files:**

- Modify: `api/app/domain/services/knowledge_base/graph_builder.py`
- Modify: `api/app/infrastructure/models/knowledge_base.py`
- Modify: `api/app/infrastructure/repositories/db_knowledge_base_repository.py`
- Modify: `api/app/domain/services/tools/knowledge_base_tools.py`
- Modify: `api/app/interfaces/schemas/knowledge_base.py`
- Modify: `api/app/interfaces/endpoints/knowledge_base_routes.py`
- Test: `api/tests/app/domain/services/knowledge_base/test_versioned_graph_builder.py`
- Test: `api/tests/app/interfaces/endpoints/test_knowledge_graph_routes.py`

**Interfaces:**

- Produces: `KnowledgeGraphResponse(nodes, edges, capability, next_cursor)`.
- Produces: atomic entity key `(version_id, normalized_name, entity_type)`.
- Consumes: build-level `max_chunks`, `max_llm_calls`, `max_tokens`, and `deadline_seconds`.

- [ ] **Step 1: Write graph identity, endpoint, and budget tests**

```python
@pytest.mark.asyncio
async def test_concurrent_entity_upsert_creates_one_entity(repo):
    await asyncio.gather(
        repo.upsert_entity("kbv2", "OpenCitadel", "product"),
        repo.upsert_entity("kbv2", "opencitadel", "product"),
    )
    assert await repo.count_entities("kbv2", normalized_name="opencitadel") == 1


@pytest.mark.asyncio
async def test_graph_response_resolves_both_edge_endpoints(client, graph):
    data = client.get("/api/knowledge-bases/kb1/versions/kbv2/graph?q=OpenCitadel").json()["data"]
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in data["edges"])


@pytest.mark.asyncio
async def test_graph_budget_marks_partial(builder):
    outcome = await builder.build("kbv2", chunks(100), budget=GraphBudget(max_llm_calls=3))
    assert outcome.calls == 3
    assert outcome.degraded_reason == "GRAPH_PARTIAL"
```

- [ ] **Step 2: Run graph tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_versioned_graph_builder.py tests/app/interfaces/endpoints/test_knowledge_graph_routes.py -q`

Expected: FAIL on uniqueness, missing endpoints, and build-level budget.

- [ ] **Step 3: Add version identity, atomic upsert, and bounded queue**

Use the migration-created `normalized_name`, `version_id`, and unique `(version_id, normalized_name, type)` identity. Use `INSERT ... ON CONFLICT ... DO UPDATE/NOTHING RETURNING id`; remove select-then-insert. Replace one task per selected chunk with a producer-consumer queue of size `2 * concurrency`. Stop scheduling when any budget limit is reached and persist a resumable cursor in build metrics.

Graph endpoint queries matched nodes, their edges, then fetches all missing endpoint nodes and evidence refs before serializing.

- [ ] **Step 4: Run graph builder, repository, tool, and route tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base/test_versioned_graph_builder.py tests/app/domain/services/knowledge_base/test_graph_builder_merge.py tests/app/infrastructure/repositories/test_db_knowledge_base_repository.py tests/app/domain/services/tools/test_knowledge_base_tools.py tests/app/interfaces/endpoints/test_knowledge_graph_routes.py -q`

Expected: PASS; UUIDs are never used as display labels.

- [ ] **Step 5: Commit truthful GraphRAG**

```bash
git add api/app/domain/services/knowledge_base/graph_builder.py api/app/infrastructure/models/knowledge_base.py api/app/infrastructure/repositories/db_knowledge_base_repository.py api/app/domain/services/tools/knowledge_base_tools.py api/app/interfaces/schemas/knowledge_base.py api/app/interfaces/endpoints/knowledge_base_routes.py api/tests/app/domain/services/knowledge_base api/tests/app/infrastructure/repositories/test_db_knowledge_base_repository.py api/tests/app/interfaces/endpoints/test_knowledge_graph_routes.py
git commit -m "feat(knowledge): make graph data versioned and evidence based"
```

## Task 7: Add Cursor-Based Document Source Reading and Clear Stale Errors

**Files:**

- Modify: `api/app/infrastructure/repositories/db_knowledge_base_repository.py:188-205,331-346`
- Modify: `api/app/application/services/knowledge_base_service.py:277-296`
- Modify: `api/app/domain/services/tools/knowledge_base_tools.py:107-130`
- Modify: `api/app/interfaces/schemas/knowledge_base.py`
- Modify: `api/app/interfaces/endpoints/knowledge_base_routes.py:171-192`
- Test: `api/tests/app/application/services/test_knowledge_document_pagination.py`
- Test: `api/tests/app/infrastructure/repositories/test_db_knowledge_document_status.py`

**Interfaces:**

- Produces: `DocumentPage(items, next_cursor, total, truncated)`.
- Produces: explicit `UNSET` sentinel so error/warning can be preserved or cleared intentionally.

- [ ] **Step 1: Write pagination and status-clear tests**

```python
@pytest.mark.asyncio
async def test_successful_retry_clears_old_error_and_warning(repo, failed_revision):
    await repo.update_revision_status(
        failed_revision.id,
        DocumentRevisionState.INDEXED,
        error=None,
        warning=None,
    )
    loaded = await repo.get_revision(failed_revision.id)
    assert loaded.error is None
    assert loaded.warning is None


@pytest.mark.asyncio
async def test_document_page_reports_next_cursor(service, long_document):
    page = await service.read_document(long_document.id, version_id="kbv2", limit=30)
    assert len(page.items) == 30
    assert page.next_cursor is not None
    assert page.truncated is True
```

- [ ] **Step 2: Run source/status tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_knowledge_document_pagination.py tests/app/infrastructure/repositories/test_db_knowledge_document_status.py -q`

Expected: FAIL because reads silently limit and None means “do not update”.

- [ ] **Step 3: Implement explicit patch semantics and cursor reads**

Use `UNSET = object()` parameters for repository patches; passing `None` writes SQL NULL. Cursor encodes `(page_no, ordinal, chunk_id)` and is validated against the requested version/revision. Return only parent chunks and include total/truncated. Update the tool to mention `next_cursor` and accept a cursor argument.

- [ ] **Step 4: Run source, tool, and repository suites**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_knowledge_document_pagination.py tests/app/infrastructure/repositories/test_db_knowledge_document_status.py tests/app/domain/services/tools/test_knowledge_base_tools.py tests/app/infrastructure/repositories/test_db_knowledge_base_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit source pagination**

```bash
git add api/app/infrastructure/repositories/db_knowledge_base_repository.py api/app/application/services/knowledge_base_service.py api/app/domain/services/tools/knowledge_base_tools.py api/app/interfaces/schemas/knowledge_base.py api/app/interfaces/endpoints/knowledge_base_routes.py api/tests/app/application/services/test_knowledge_document_pagination.py api/tests/app/infrastructure/repositories/test_db_knowledge_document_status.py
git commit -m "feat(knowledge): paginate immutable document sources"
```

## Task 8: Expose Versions, Builds, Real Graph, and Ask/Agent UX

**Files:**

- Modify: `api/app/interfaces/schemas/knowledge_base.py`
- Modify: `api/app/interfaces/endpoints/knowledge_base_routes.py`
- Modify: `ui/src/lib/api/knowledge.ts`
- Modify: `ui/src/lib/api/types.ts`
- Create: `ui/src/components/knowledge/knowledge-version-status.tsx`
- Create: `ui/src/components/knowledge/knowledge-graph.tsx`
- Create: `ui/src/components/knowledge/document-pager.tsx`
- Modify: `ui/src/components/knowledge/knowledge-library.tsx`
- Modify: `ui/src/components/workspace/knowledge-context-panel.tsx`
- Modify: `ui/src/components/knowledge/knowledge-detail-redirect.tsx`
- Test: `ui/src/components/knowledge/knowledge-version-status.test.tsx`
- Test: `ui/src/components/workspace/knowledge-context-panel.test.tsx`

**Interfaces:**

- Consumes: version, build, graph, cursor, and binding endpoints.
- Produces: explicit version/degraded UI and real graph rendering.

- [ ] **Step 1: Write UI contract tests**

```tsx
it("offers both ask and agent for a published version", async () => {
  render(<KnowledgeLibrary api={readyVersionApi} />);
  await user.click(screen.getByRole("button", { name: "开始 Agent" }));
  expect(sessionApi.createSession).toHaveBeenCalledWith(
    expect.objectContaining({ knowledge_base_id: "kb1", mode: "agent", knowledge_base_version_id: "kbv2" }),
  );
});

it("renders entity relations, not document title placeholders", async () => {
  render(<KnowledgeContextPanel knowledgeBaseId="kb1" versionId="kbv2" />);
  expect(await screen.findByText("OpenCitadel")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-edge-e1")).toHaveAttribute("data-source", "entity-1");
});

it("loads the next source page", async () => {
  render(<DocumentPager initialPage={pageWithCursor} />);
  await user.click(screen.getByRole("button", { name: "继续加载" }));
  expect(knowledgeApi.readDocument).toHaveBeenLastCalledWith(
    "kb1", "kbv2", "doc1", expect.objectContaining({ cursor: "cursor-2" }),
  );
});
```

- [ ] **Step 2: Run the UI tests**

Run: `cd ui && npm test -- --run src/components/knowledge/knowledge-version-status.test.tsx src/components/workspace/knowledge-context-panel.test.tsx`

Expected: FAIL because versions, Agent entry, real graph, and pagination UI do not exist.

- [ ] **Step 3: Implement version-aware resource UI**

Add endpoints:

```text
GET  /knowledge-bases/{id}/versions
GET  /knowledge-bases/{id}/versions/{version_id}
GET  /knowledge-bases/{id}/versions/{version_id}/graph
GET  /knowledge-bases/{id}/versions/{version_id}/documents/{doc_id}/content
POST /knowledge-bases/{id}/builds
POST /knowledge-bases/{id}/builds/{build_id}/retry
POST /knowledge-bases/{id}/builds/{build_id}/cancel
```

The list shows active version plus candidate build. Disable Ask/Agent only when there is no published version; an in-progress candidate does not disable the active version. Replace `graphForDocs()` with API-backed nodes/edges. The detail redirect creates Ask with the active version explicitly; the library offers both modes.

- [ ] **Step 4: Run UI and endpoint tests**

Run: `cd api && .venv/bin/pytest tests/app/interfaces/endpoints/test_knowledge_base_routes.py tests/app/interfaces/endpoints/test_knowledge_graph_routes.py -q`

Run: `cd ui && npm test -- --run src/components/knowledge src/components/workspace/knowledge-context-panel.test.tsx`

Expected: PASS; no component constructs a document-title Mermaid graph.

- [ ] **Step 5: Commit knowledge version UX**

```bash
git add api/app/interfaces/schemas/knowledge_base.py api/app/interfaces/endpoints/knowledge_base_routes.py ui/src/lib/api/knowledge.ts ui/src/lib/api/types.ts ui/src/components/knowledge ui/src/components/workspace/knowledge-context-panel.tsx ui/src/components/workspace/knowledge-context-panel.test.tsx
git commit -m "feat(knowledge): expose versioned ask agent and graph ux"
```

## Task 9: Add Version Retention and Safe Garbage Collection

**Files:**

- Create: `api/app/application/services/resource_version_gc_service.py`
- Modify: `api/app/infrastructure/repositories/db_knowledge_version_repository.py`
- Modify: `api/app/infrastructure/external/scheduler/job_scheduler.py`
- Modify: `api/config.yaml`
- Test: `api/tests/app/application/services/test_knowledge_version_gc.py`

**Interfaces:**

- Consumes: current session bindings and active version.
- Produces: `collect_knowledge_versions(retain_count, min_age_days, batch_size)`.

- [ ] **Step 1: Write binding-safe GC tests**

```python
@pytest.mark.asyncio
async def test_gc_keeps_active_and_bound_versions(gc, versions):
    deleted = await gc.collect_knowledge_versions(retain_count=2, min_age_days=30, batch_size=50)
    assert versions.active.id not in deleted
    assert versions.bound_old.id not in deleted
    assert versions.recent.id not in deleted
    assert versions.unbound_expired.id in deleted
```

- [ ] **Step 2: Run GC tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_knowledge_version_gc.py -q`

Expected: FAIL because physical deletion currently follows logical document/KB deletion.

- [ ] **Step 3: Implement bounded, reference-aware GC**

Select only versions that are:

- not active;
- not referenced by any session binding;
- older than `knowledge_base.version_retention_min_days`;
- beyond `knowledge_base.version_retention_count`;
- not attached to a queued/running build.

Delete at most `knowledge_base.version_gc_batch_size` versions per scheduler tick. Delete version-scoped graph/chunks/manifest/revisions only when no other version references a revision. Record counts and bytes in audit metrics.

- [ ] **Step 4: Run GC, delete, and binding tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_knowledge_version_gc.py tests/app/application/services/test_knowledge_base_service.py tests/app/application/services/test_resource_binding_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit retention**

```bash
git add api/app/application/services/resource_version_gc_service.py api/app/infrastructure/repositories/db_knowledge_version_repository.py api/app/infrastructure/external/scheduler/job_scheduler.py api/config.yaml api/tests/app/application/services/test_knowledge_version_gc.py
git commit -m "feat(knowledge): garbage collect unreferenced versions safely"
```

## Task 10: Verify KB Invariants and Update Documentation

**Files:**

- Create: `api/tests/app/contracts/test_kb_version_invariants.py`
- Modify: `docs/architecture/knowledge-base-ingestion.zh-CN.md`
- Modify: `docs/architecture/knowledge-base-ingestion.md`
- Modify: `docs/tutorials/02-internal-knowledge-base.zh-CN.md`
- Modify: `docs/tutorials/02-internal-knowledge-base.md`

**Interfaces:**

- Consumes: all Tasks 1-9.
- Produces: executable KB acceptance gate.

- [ ] **Step 1: Add full failure-injection and history tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [
    "parse", "chunk", "keyword_index", "vector_index", "graph", "validate", "publish",
])
async def test_active_version_remains_queryable_for_every_build_failure(kb_system, phase):
    before = await kb_system.query("kb1", "kbv1", "known")
    outcome = await kb_system.rebuild("kb1", fail_at=phase)
    after = await kb_system.query("kb1", "kbv1", "known")
    if phase in {"vector_index", "graph"}:
        assert outcome.state == BuildState.DEGRADED
    else:
        assert outcome.state == BuildState.FAILED
    assert after == before


@pytest.mark.asyncio
async def test_old_session_survives_document_removal(kb_system):
    old = await kb_system.bind_session("s-old", "kb1", "kbv1")
    await kb_system.remove_and_publish("kb1", "doc1")
    assert await kb_system.read_source(old, "doc1")
    assert not await kb_system.active_version_contains("kb1", "doc1")
```

- [ ] **Step 2: Run the KB contract suite**

Run: `cd api && .venv/bin/pytest tests/app/contracts/test_kb_version_invariants.py -q`

Expected: PASS; fix the owning task if a case fails.

- [ ] **Step 3: Rewrite the authoritative ingestion documentation**

Document:

- immutable version/revision/manifest state machines;
- mandatory versus degraded phases;
- atomic publication and failure behavior;
- Ask/Agent session binding and upgrade behavior;
- versioned citation and source pagination;
- true GraphRAG API and budget;
- migration/compatibility and retention rules.

Remove statements that reindex clears the active index or that parsed `READY` alone permits Ask.

- [ ] **Step 4: Run all KB, endpoint, migration, and UI verification**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/knowledge_base tests/app/application/services/test_knowledge_base_service.py tests/app/application/services/test_knowledge_version_service.py tests/app/infrastructure/repositories/test_db_knowledge_base_repository.py tests/app/infrastructure/repositories/test_db_knowledge_version_repository.py tests/app/interfaces/endpoints/test_knowledge_base_routes.py tests/app/contracts/test_kb_version_invariants.py -q`

Run: `cd ui && npm test -- --run src/components/knowledge src/components/workspace/knowledge-context-panel.test.tsx`

Run: `cd api && .venv/bin/alembic heads`

Expected: all tests PASS and Alembic prints exactly `c7d8e9f0a1b2 (head)`.

- [ ] **Step 5: Commit KB verification and docs**

```bash
git add api/tests/app/contracts/test_kb_version_invariants.py docs/architecture/knowledge-base-ingestion.zh-CN.md docs/architecture/knowledge-base-ingestion.md docs/tutorials/02-internal-knowledge-base.zh-CN.md docs/tutorials/02-internal-knowledge-base.md
git commit -m "docs: document versioned knowledge ingestion"
```

## Completion Gate

The knowledge-base line is complete only when:

- core build failures never alter active-version search or source reads;
- parsed-but-unindexed revisions cannot create sessions or appear in retrieval;
- vector and graph failures publish visible degraded capabilities without losing BM25;
- old bound sessions still resolve removed documents from their old version;
- graph responses contain real entities/relations and complete evidence-bearing endpoints;
- long source documents can be fully paged;
- both Ask and Agent bind a concrete published KB version;
- migration `c7d8e9f0a1b2` is the sole Alembic head.
