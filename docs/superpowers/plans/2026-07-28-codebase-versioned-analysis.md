# Codebase Versioned Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build immutable, securely acquired codebase versions with atomic analysis publication, hybrid lexical/vector retrieval, reproducible source reads, and evidence-backed analysis artifacts.

**Architecture:** Every codebase import/reanalysis creates a candidate `codebase_version`, materializes into a clean temporary workspace, validates and snapshots the source, and writes all files/symbols/edges/chunks/artifacts under that version. Keyword search is mandatory, vector and advanced analysis are degradable, and a short compare-and-swap transaction publishes the candidate while old sessions keep their bound snapshot.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, PostgreSQL full-text search/pgvector, sandbox and object storage ports, tree-sitter language pack, pytest, Next.js/React/TypeScript, Vitest.

## Global Constraints

- Requires completion of the Agent/shared plan and the KB migration; Alembic head starts at `c7d8e9f0a1b2`.
- A publishable codebase version has a non-empty source set, mandatory lexical index, immutable snapshot, source digest, and validated referential closure.
- Reanalysis never clears or modifies active-version rows.
- One codebase has at most one queued/running build; duplicate reanalyze returns that build.
- Vector failure automatically falls back to lexical search and remains visibly degraded.
- Source reads are canonical-path contained and use the bound version snapshot, not a long-lived ingestion sandbox.
- Architecture, data flow, call chain, and flowchart facts require `EvidenceRef`; unsupported views are omitted.
- Git is HTTPS-only in the first secure implementation and must pass public-host/IP validation.
- Existing APIs remain compatible for one release and project the active version.
- Existing worktree changes must not be included in task commits.

---

## File Structure

### New files

- `api/app/domain/models/codebase_version.py` — version, capabilities, source snapshot, EvidenceRef, parser confidence.
- `api/app/domain/repositories/codebase_version_repository.py` — candidate/version-scoped repository contract.
- `api/app/domain/services/codebase/source_validator.py` — source parameter, ownership, URL, ZIP, and path safety.
- `api/app/domain/services/codebase/snapshot_service.py` — immutable snapshot creation, indexed safe reads, and restore.
- `api/app/domain/services/codebase/version_builder.py` — candidate/build creation, validation, compare-and-swap publish.
- `api/app/domain/services/codebase/lexical_indexer.py` — identifier-aware search documents.
- `api/app/domain/services/codebase/hybrid_retriever.py` — vector/lexical RRF and degraded metadata.
- `api/app/domain/services/codebase/parsers/` — parser adapters and evidence-bearing symbols/edges.
- `api/app/application/services/codebase_version_service.py` — ResourceVersionProvider, list/build/retry/cancel.
- `api/app/infrastructure/models/codebase_version.py` — SQLAlchemy version model.
- `api/app/infrastructure/repositories/db_codebase_version_repository.py` — version-scoped PostgreSQL access.
- `api/alembic/versions/d8e9f0a1b2c3_add_codebase_versions.py` — version schema, search index, v1 backfill.
- `api/tests/app/contracts/test_codebase_version_invariants.py` — security, failure, history, evidence suite.
- `ui/src/components/codebase/codebase-version-status.tsx` — active/candidate/degraded UI.
- `ui/src/components/codebase/code-evidence-panel.tsx` — source-linked graph evidence.

### Existing files with focused modifications

- `api/app/infrastructure/models/codebase.py` — active version and version-scoped rows.
- `api/app/domain/repositories/{codebase_repository,uow}.py` and DB implementations — version APIs.
- `api/app/application/services/codebase_service.py` — secure commands and binding-aware reads/attach.
- `api/app/domain/services/codebase/{ingestion_runner,indexer,static_analyzer,artifact_generator}.py` — staged version analysis.
- `api/app/domain/services/tools/codebase_tools.py` — bound source reader and hybrid retrieval.
- `api/app/application/services/task_runner_factory.py` — resolve bound version for CodebaseTool/Agent attach.
- `api/app/interfaces/schemas/codebase.py` and `api/app/interfaces/endpoints/codebase_routes.py` — versions/builds/evidence endpoints.
- `api/app/worker/main.py` — Codebase build execution/reconciliation.
- `api/pyproject.toml` and `api/uv.lock` — tree-sitter language pack.
- `ui/src/lib/api/{codebase,types}.ts` — version/build/search/evidence contracts.
- `ui/src/components/codebase/{codebase-library,codebase-detail-redirect}.tsx` — version-aware UX.

## Task 1: Add Codebase Version Schema, Search Columns, and v1 Backfill

**Files:**

- Create: `api/app/domain/models/codebase_version.py`
- Create: `api/app/infrastructure/models/codebase_version.py`
- Create: `api/alembic/versions/d8e9f0a1b2c3_add_codebase_versions.py`
- Modify: `api/app/infrastructure/models/codebase.py`
- Modify: `api/app/infrastructure/models/__init__.py`
- Test: `api/tests/app/alembic/test_codebase_version_migration.py`

**Interfaces:**

- Consumes: shared `ResourceBuild`.
- Produces: `CodebaseVersion`, `CodeEvidenceRef`, `AnalysisCapability`.
- Produces: `codebases.active_version_id`.

- [ ] **Step 1: Write the schema/backfill test**

```python
def test_existing_codebase_becomes_legacy_published_v1(migrated_db, legacy_codebase):
    active = migrated_db.scalar(
        "SELECT active_version_id FROM codebases WHERE id=:id",
        {"id": legacy_codebase.id},
    )
    version = migrated_db.fetch_one(
        "SELECT state, legacy_snapshot, vector_degraded FROM codebase_versions WHERE id=:id",
        {"id": active},
    )
    assert version.state in {"ready", "degraded"}
    assert version.legacy_snapshot is True
    assert migrated_db.scalar(
        "SELECT count(*) FROM codebase_files WHERE codebase_id=:id AND version_id IS NULL",
        {"id": legacy_codebase.id},
    ) == 0
    assert migrated_db.scalar(
        """
        SELECT count(*) FROM session_resource_bindings
        WHERE session_id=:session_id
          AND resource_kind='codebase'
          AND version_id=:version_id
          AND is_current=true
        """,
        {"session_id": legacy_codebase.session_id, "version_id": active},
    ) == 1
```

- [ ] **Step 2: Run the migration test**

Run: `cd api && .venv/bin/pytest tests/app/alembic/test_codebase_version_migration.py -q`

Expected: FAIL because codebase version schema is absent.

- [ ] **Step 3: Create migration `d8e9f0a1b2c3`**

Create:

```text
codebase_versions(
  id, codebase_id, parent_version_id, build_id, state,
  source_snapshot_key, source_revision, source_digest,
  capabilities jsonb, degraded_reasons jsonb, metrics jsonb,
  legacy_snapshot boolean, created_at, published_at
)
```

Add `active_version_id` to codebases and `version_id` to files, symbols, edges, chunks, and artifacts. Add symbol columns `qualified_name`, `parser`, `confidence`; edge columns `resolution`, `confidence`, `evidence jsonb`; chunk columns `search_text` and generated/stored `search_vector`. Index:

- unique `(version_id, path)` for files;
- `(version_id, qualified_name)` for symbols;
- GIN on `search_vector`;
- vector index scoped/filterable by version;
- `(version_id, src_symbol_id)` and `(version_id, dst_symbol_id)` for edges.

Backfill one legacy version per codebase and assign all existing analysis rows. Copy `snapshot_key` and `vector_degraded` into the version. Backfill a current `session_resource_bindings` row for every legacy session with `codebase_id`; retain the legacy session column during the compatibility window. Use `down_revision = "c7d8e9f0a1b2"`.

- [ ] **Step 4: Exercise migration upgrade/downgrade**

Run: `cd api && .venv/bin/alembic upgrade d8e9f0a1b2c3`

Run: `cd api && .venv/bin/pytest tests/app/alembic/test_codebase_version_migration.py -q`

Run: `cd api && .venv/bin/alembic downgrade c7d8e9f0a1b2 && .venv/bin/alembic upgrade d8e9f0a1b2c3`

Expected: all commands succeed and the test PASSes.

- [ ] **Step 5: Commit the codebase version schema**

```bash
git add api/app/domain/models/codebase_version.py api/app/infrastructure/models/codebase_version.py api/app/infrastructure/models/codebase.py api/app/infrastructure/models/__init__.py api/alembic/versions/d8e9f0a1b2c3_add_codebase_versions.py api/tests/app/alembic/test_codebase_version_migration.py
git commit -m "feat(codebase): add immutable analysis versions"
```

## Task 2: Validate Source Parameters, Ownership, Git Targets, ZIPs, and Paths

**Files:**

- Create: `api/app/domain/services/codebase/source_validator.py`
- Modify: `api/app/interfaces/schemas/codebase.py:19-24,82-85`
- Modify: `api/app/application/services/codebase_service.py:79-120,261-279`
- Modify: `api/app/interfaces/endpoints/codebase_routes.py:42-57,122-142`
- Test: `api/tests/app/domain/services/codebase/test_source_validator.py`
- Test: `api/tests/app/application/services/test_codebase_source_security.py`

**Interfaces:**

- Produces: `ValidatedCodebaseSource`.
- Produces: `CodebaseSourceValidator.validate_create(...)`.
- Produces: `normalize_contained_path(root, requested) -> PurePosixPath`.

- [ ] **Step 1: Write source and traversal security tests**

```python
@pytest.mark.parametrize(
    "payload",
    [
        {"source_type": "zip", "file_id": None},
        {"source_type": "files", "file_ids": []},
        {"source_type": "git", "git_url": ""},
    ],
)
def test_missing_source_payload_is_rejected(client, payload):
    assert client.post("/api/codebases", json=payload).status_code == 422


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ssh://git@host/repo",
    "https://127.0.0.1/repo.git",
    "https://169.254.169.254/latest/meta-data",
])
def test_unsafe_git_url_is_rejected(validator, url):
    with pytest.raises(BadRequestError):
        validator.validate_git_url(url)


@pytest.mark.parametrize("path", ["../secret", "src/../../secret", "/etc/passwd"])
def test_source_path_cannot_escape_root(path):
    with pytest.raises(BadRequestError):
        normalize_contained_path("/workspace/codebase", path)
```

Add ZIP cases for absolute members, `..`, symlink members, entry count, total uncompressed size, and compression ratio.

- [ ] **Step 2: Run security tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_source_validator.py tests/app/application/services/test_codebase_source_security.py -q`

Expected: FAIL because source fields are optional and paths/URLs are concatenated.

- [ ] **Step 3: Implement one validation service at the application boundary**

`validate_create()` must:

- ZIP: require one owned/downloadable file and validate archive metadata before dispatch.
- FILES: require at least one unique owned/downloadable file.
- GIT: allow only `https`, reject credentials, non-default ports unless configured, resolve every address and reject private/loopback/link-local/multicast/metadata networks.

Use Pydantic `model_validator(mode="after")` for shape errors and repeat ownership/security checks in the service. `normalize_contained_path()` uses `PurePosixPath`, rejects absolute/`..`, joins to root, and verifies `commonpath` equality.

- [ ] **Step 4: Run source, API schema, ownership, and traversal tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_source_validator.py tests/app/application/services/test_codebase_source_security.py tests/app/interfaces/endpoints/test_codebase_routes.py -q`

Expected: PASS; unsafe inputs are rejected before a task or sandbox is created.

- [ ] **Step 5: Commit source boundary security**

```bash
git add api/app/domain/services/codebase/source_validator.py api/app/interfaces/schemas/codebase.py api/app/application/services/codebase_service.py api/app/interfaces/endpoints/codebase_routes.py api/tests/app/domain/services/codebase/test_source_validator.py api/tests/app/application/services/test_codebase_source_security.py
git commit -m "fix(codebase): validate source acquisition and paths"
```

## Task 3: Materialize Every Build in a Clean Workspace and Create an Immutable Snapshot

**Files:**

- Create: `api/app/domain/services/codebase/snapshot_service.py`
- Modify: `api/app/domain/services/codebase/ingestion_runner.py:170-225`
- Modify: `api/app/domain/external/sandbox.py`
- Modify: `api/app/infrastructure/external/sandbox/docker_sandbox.py`
- Modify: `api/app/infrastructure/external/sandbox/kubernetes_sandbox.py`
- Test: `api/tests/app/domain/services/codebase/test_snapshot_service.py`
- Test: `api/tests/app/domain/services/codebase/test_clean_materialization.py`

**Interfaces:**

- Consumes: `ValidatedCodebaseSource`.
- Produces: `MaterializedSource(workspace, source_revision, source_digest, snapshot_key)`.
- Produces: `CodeSnapshotService.create()`, `restore()`, and `read_file()`.

- [ ] **Step 1: Write stale-file, command-quoting, and snapshot tests**

```python
@pytest.mark.asyncio
async def test_files_reanalysis_uses_empty_workspace(materializer):
    first = await materializer.materialize(files={"old.py": "old", "keep.py": "v1"})
    second = await materializer.materialize(files={"keep.py": "v2"})
    assert not await second.sandbox.exists(second.workspace / "old.py")
    assert await second.sandbox.read(second.workspace / "keep.py") == "v2"


@pytest.mark.asyncio
async def test_git_clone_command_cannot_interpolate_shell(materializer, sandbox):
    await materializer.materialize_git("https://example.com/repo.git;touch /tmp/pwned")
    assert sandbox.commands == []


@pytest.mark.asyncio
async def test_snapshot_is_content_addressed(snapshot_service, source_tree):
    a = await snapshot_service.create("cbv1", source_tree)
    b = await snapshot_service.create("cbv2", source_tree)
    assert a.source_digest == b.source_digest
    assert a.snapshot_key == b.snapshot_key
```

- [ ] **Step 2: Run materialization tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_snapshot_service.py tests/app/domain/services/codebase/test_clean_materialization.py -q`

Expected: FAIL because workspaces are reused and snapshot generation is download-triggered.

- [ ] **Step 3: Implement clean materialization and source-addressed snapshots**

Create a unique workspace `/home/ubuntu/codebase-builds/<build_id>` for every build. For Git, revalidate the already-normalized HTTPS URL and execute:

```python
url_arg = shlex.quote(validated.git_url)
workspace_arg = shlex.quote(workspace)
command = f"git -c http.followRedirects=false clone --depth 1 -- {url_arg} {workspace_arg}"
```

ZIPs are extracted only after server-side member validation. FILES use validated filenames and contained paths. After materialization, calculate a stable digest over sorted `(relative_path, file_sha256)` entries, create `codebase-snapshots/sha256/<digest>.tgz`, and persist snapshot metadata before analysis begins.

Extend the sandbox port only with narrowly scoped operations needed by the implementation; do not expose an arbitrary host filesystem.

- [ ] **Step 4: Run materialization, runner, and sandbox contract tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_snapshot_service.py tests/app/domain/services/codebase/test_clean_materialization.py tests/app/domain/services/codebase/test_codebase_ingestion_runner.py tests/app/infrastructure/external/sandbox -q`

Expected: PASS; every version has a snapshot before analysis.

- [ ] **Step 5: Commit clean snapshots**

```bash
git add api/app/domain/services/codebase/snapshot_service.py api/app/domain/services/codebase/ingestion_runner.py api/app/domain/external/sandbox.py api/app/infrastructure/external/sandbox/docker_sandbox.py api/app/infrastructure/external/sandbox/kubernetes_sandbox.py api/tests/app/domain/services/codebase/test_snapshot_service.py api/tests/app/domain/services/codebase/test_clean_materialization.py api/tests/app/infrastructure/external/sandbox/test_docker_sandbox_snapshot.py api/tests/app/infrastructure/external/sandbox/test_kubernetes_sandbox_snapshot.py
git commit -m "feat(codebase): snapshot clean source workspaces"
```

## Task 4: Implement Candidate Builds, Version Repository, and Atomic Publish

**Files:**

- Create: `api/app/domain/repositories/codebase_version_repository.py`
- Create: `api/app/infrastructure/repositories/db_codebase_version_repository.py`
- Create: `api/app/domain/services/codebase/version_builder.py`
- Create: `api/app/application/services/codebase_version_service.py`
- Modify: `api/app/domain/repositories/uow.py`
- Modify: `api/app/infrastructure/repositories/db_uow.py`
- Modify: `api/app/application/services/codebase_service.py:190-206`
- Modify: `api/app/domain/services/codebase/ingestion_runner.py:45-159`
- Modify: `api/app/worker/main.py`
- Test: `api/tests/app/infrastructure/repositories/test_db_codebase_version_repository.py`
- Test: `api/tests/app/application/services/test_codebase_version_service.py`
- Test: `api/tests/app/application/services/test_codebase_service_reanalyze.py`
- Test: `api/tests/app/domain/services/codebase/test_version_builder.py`
- Test: `api/tests/app/domain/services/codebase/test_versioned_ingestion_runner.py`
- Test: `api/tests/app/worker/test_codebase_build_reconciliation.py`

**Interfaces:**

- Produces: `CodebaseVersionService` implementing `ResourceVersionProvider`.
- Produces: repository `publish_candidate(version_id, expected_active_version_id, ...) -> bool`.
- Consumes: shared ResourceBuildService.

- [ ] **Step 1: Write idempotency, failure, and reconciliation tests**

```python
@pytest.mark.asyncio
async def test_duplicate_reanalyze_returns_same_build(service, ready_codebase):
    first = await service.reanalyze(ready_codebase.id, scope)
    second = await service.reanalyze(ready_codebase.id, scope)
    assert first.build_id == second.build_id
    assert first.version_id == second.version_id


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["materialize", "analyze", "keyword_index", "validate", "publish"])
async def test_build_failure_keeps_active_rows(runner, codebase_v1, phase):
    before = await snapshot_analysis(codebase_v1.id)
    await collect(runner.run(build_for(codebase_v1, fail_at=phase)))
    assert await active_version_id(codebase_v1.codebase_id) == codebase_v1.id
    assert await snapshot_analysis(codebase_v1.id) == before


@pytest.mark.asyncio
async def test_stale_codebase_build_is_failed_without_changing_active(reconciler, stale_build):
    await reconciler.run()
    assert await build_state(stale_build.id) == BuildState.FAILED
    assert await active_version_id(stale_build.resource_id) == stale_build.parent_version_id


@pytest.mark.asyncio
async def test_provider_rejects_non_published_or_foreign_version(service, scope):
    with pytest.raises(BadRequestError):
        await service.resolve_published_version("cb1", "building-cbv2", scope)
    with pytest.raises(NotFoundError):
        await service.resolve_published_version("cb1", "other-codebase-v1", scope)
```

- [ ] **Step 2: Run versioned build tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/repositories/test_db_codebase_version_repository.py tests/app/application/services/test_codebase_version_service.py tests/app/domain/services/codebase/test_version_builder.py tests/app/domain/services/codebase/test_versioned_ingestion_runner.py tests/app/worker/test_codebase_build_reconciliation.py -q`

Expected: FAIL because reanalyze creates concurrent tasks and Runner clears shared rows.

- [ ] **Step 3: Write all analysis rows to candidate version and publish by CAS**

The Runner entry becomes `run(build_id)`. Remove `clear_analysis_data(codebase_id)`; retries may only clear rows where `version_id == candidate.id`. Save files, symbols, edges, chunks, and artifacts with candidate version. Validate:

- snapshot exists and digest matches;
- at least one eligible source file;
- keyword index document count matches indexable files/symbols;
- every symbol references a candidate file;
- every resolved edge references candidate symbols;
- every artifact evidence points to a candidate file/symbol.

Publish by locking the logical codebase and comparing active ID to parent ID. Set capabilities/degraded reasons on the version. Shared Worker reconciliation handles stale `resource_kind=codebase` builds.

- [ ] **Step 4: Run version repository, runner, reanalyze, and worker tests**

Run: `cd api && .venv/bin/pytest tests/app/infrastructure/repositories/test_db_codebase_version_repository.py tests/app/application/services/test_codebase_version_service.py tests/app/domain/services/codebase/test_version_builder.py tests/app/domain/services/codebase/test_versioned_ingestion_runner.py tests/app/worker/test_codebase_build_reconciliation.py tests/app/application/services/test_codebase_service_reanalyze.py -q`

Expected: PASS; concurrent reanalyze creates one active build and no shared clear occurs.

- [ ] **Step 5: Commit atomic codebase builds**

```bash
git add api/app/domain/repositories/codebase_version_repository.py api/app/infrastructure/repositories/db_codebase_version_repository.py api/app/domain/services/codebase/version_builder.py api/app/application/services/codebase_version_service.py api/app/domain/repositories/uow.py api/app/infrastructure/repositories/db_uow.py api/app/application/services/codebase_service.py api/app/domain/services/codebase/ingestion_runner.py api/app/worker/main.py api/tests/app/infrastructure/repositories/test_db_codebase_version_repository.py api/tests/app/application/services/test_codebase_version_service.py api/tests/app/application/services/test_codebase_service_reanalyze.py api/tests/app/domain/services/codebase/test_version_builder.py api/tests/app/domain/services/codebase/test_versioned_ingestion_runner.py api/tests/app/worker/test_codebase_build_reconciliation.py
git commit -m "feat(codebase): publish versioned analysis atomically"
```

## Task 5: Filter During Traversal, Batch Source Reads, and Reject Empty Analysis

**Files:**

- Modify: `api/app/domain/services/codebase/ingestion_runner.py:63-72,227-272`
- Modify: `api/app/domain/services/codebase/static_analyzer.py:20-84`
- Modify: `api/app/domain/external/sandbox.py`
- Modify: `api/app/infrastructure/external/sandbox/docker_sandbox.py`
- Modify: `api/app/infrastructure/external/sandbox/kubernetes_sandbox.py`
- Test: `api/tests/app/domain/services/codebase/test_source_collection.py`
- Test: `api/tests/app/domain/services/codebase/test_versioned_ingestion_runner.py`
- Test: `api/tests/app/infrastructure/external/sandbox/test_source_collection.py`

**Interfaces:**

- Produces: `SourceCollectionResult(entries, scanned, skipped, failed, truncated, total_bytes)`.
- Consumes: source limits from runtime config.

- [ ] **Step 1: Write starvation, batching, and empty-source tests**

```python
@pytest.mark.asyncio
async def test_ignored_files_do_not_consume_source_limit(collector):
    tree = {f"node_modules/p{i}.js": "x" for i in range(6000)}
    tree["src/main.py"] = "def main(): pass"
    result = await collector.collect(tree, max_files=5000)
    assert [entry.path for entry in result.entries] == ["src/main.py"]


@pytest.mark.asyncio
async def test_collection_uses_bounded_batches(collector, sandbox):
    await collector.collect(source_files(250), batch_size=50)
    assert sandbox.batch_read_calls == 5


@pytest.mark.asyncio
async def test_no_indexable_source_fails_build(runner, empty_source_build):
    outcome = await run_to_outcome(runner, empty_source_build)
    assert outcome.state == BuildState.FAILED
    assert outcome.error_code == "CODEBASE_NO_INDEXABLE_SOURCE"
```

- [ ] **Step 2: Run source collection tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_source_collection.py tests/app/domain/services/codebase/test_versioned_ingestion_runner.py -q`

Expected: FAIL because `head -n 5000` happens before filtering and reads are sequential.

- [ ] **Step 3: Move filtering and quotas into sandbox-side traversal**

The traversal command/process prunes `IGNORE_DIRS`, filters extensions, and measures byte/file limits before returning paths. Add `sandbox.read_files(paths, max_bytes_each)` to return a bounded batch. Stop with structured `truncated=true` rather than silently dropping. Fail validation when `entries` is empty; do not generate generic artifacts.

- [ ] **Step 4: Run collection, runner, and sandbox tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_source_collection.py tests/app/domain/services/codebase/test_versioned_ingestion_runner.py tests/app/infrastructure/external/sandbox -q`

Expected: PASS; ignored directories never consume the source-file quota.

- [ ] **Step 5: Commit bounded collection**

```bash
git add api/app/domain/services/codebase/ingestion_runner.py api/app/domain/services/codebase/static_analyzer.py api/app/domain/external/sandbox.py api/app/infrastructure/external/sandbox/docker_sandbox.py api/app/infrastructure/external/sandbox/kubernetes_sandbox.py api/tests/app/domain/services/codebase/test_source_collection.py api/tests/app/domain/services/codebase/test_versioned_ingestion_runner.py api/tests/app/infrastructure/external/sandbox/test_source_collection.py
git commit -m "perf(codebase): filter and batch source collection"
```

## Task 6: Read and Restore Source from the Bound Immutable Snapshot

**Files:**

- Modify: `api/app/domain/services/codebase/snapshot_service.py`
- Modify: `api/app/application/services/codebase_service.py:261-337`
- Modify: `api/app/domain/services/tools/codebase_tools.py:181-202`
- Modify: `api/app/application/services/task_runner_factory.py:290-315,381-403`
- Modify: `api/app/domain/services/agent/sandbox_provider.py`
- Test: `api/tests/app/application/services/test_codebase_bound_source.py`
- Test: `api/tests/app/domain/services/tools/test_codebase_tools.py`

**Interfaces:**

- Consumes: session `ResourceVersionBinding`.
- Produces: `VersionedCodeSource.read(path, start_line, end_line)`.
- Produces: `CodeSourceProvenance.PUBLISHED_VERSION | SESSION_WORKSPACE`.
- Produces: Agent sentinel `.oc_codebase_attached_<codebase_id>_<version_id>_<digest>`.

- [ ] **Step 1: Write dead-ingest-sandbox, traversal, and upgrade tests**

```python
@pytest.mark.asyncio
async def test_ask_reads_snapshot_when_ingest_sandbox_is_gone(source_service, version):
    version.ingest_sandbox_id = None
    assert "def main" in await source_service.read(version.id, "src/main.py")


@pytest.mark.asyncio
async def test_bound_source_rejects_parent_escape(source_service, version):
    with pytest.raises(BadRequestError):
        await source_service.read(version.id, "../../etc/passwd")


@pytest.mark.asyncio
async def test_agent_attach_sentinel_contains_version_and_digest(attacher, session_sandbox, version):
    await attacher.attach(version, session_sandbox)
    sentinel = await session_sandbox.read(
        f"/home/ubuntu/.oc_codebase_attached_{version.codebase_id}_{version.id}_{version.source_digest}"
    )
    assert sentinel


@pytest.mark.asyncio
async def test_agent_local_edit_is_labeled_as_workspace_source(agent_code_tool, edited_workspace):
    result = await agent_code_tool.read("src/main.py")
    assert result.provenance == CodeSourceProvenance.SESSION_WORKSPACE
    assert result.base_version_id == edited_workspace.bound_version_id
```

- [ ] **Step 2: Run bound-source tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_codebase_bound_source.py tests/app/domain/services/tools/test_codebase_tools.py -q`

Expected: FAIL because reads depend on `codebase.sandbox_id` and sentinel has no revision.

- [ ] **Step 3: Replace ingestion-sandbox reads with VersionedCodeSource**

Create a safe tar member index when snapshot is stored. `read_file()` validates the requested path and reads only that member with byte/line limits. CodebaseTool receives `version_id` plus `VersionedCodeSource`; it no longer receives a mutable ingestion sandbox for Ask. Ask results are labeled `PUBLISHED_VERSION`. Agent code reads prefer its attached workspace and are labeled `SESSION_WORKSPACE` with the bound base version, so answers cannot present local edits as published-index evidence.

Agent attach restores the bound snapshot. If the expected sentinel is absent and the workspace contains local changes, return `CodebaseUpgradeConflict` rather than overwrite. Remove the catch-all warning in TaskRunnerFactory: attach failure yields a failed RunOutcome before tools execute.

- [ ] **Step 4: Run source, attach, TaskRunnerFactory, and Ask tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_codebase_bound_source.py tests/app/domain/services/tools/test_codebase_tools.py tests/app/application/services/test_task_runner_factory_codebase_attach.py tests/app/domain/services/flows/test_ask_flows.py -q`

Expected: PASS; destroying the ingest sandbox does not break source reads.

- [ ] **Step 5: Commit bound snapshot reads**

```bash
git add api/app/domain/services/codebase/snapshot_service.py api/app/application/services/codebase_service.py api/app/domain/services/tools/codebase_tools.py api/app/application/services/task_runner_factory.py api/app/domain/services/agent/sandbox_provider.py api/tests/app/application/services/test_codebase_bound_source.py api/tests/app/application/services/test_task_runner_factory_codebase_attach.py api/tests/app/domain/services/tools/test_codebase_tools.py
git commit -m "feat(codebase): read source from bound snapshots"
```

## Task 7: Add Mandatory Lexical Index and Hybrid Retrieval

**Files:**

- Create: `api/app/domain/services/codebase/lexical_indexer.py`
- Create: `api/app/domain/services/codebase/hybrid_retriever.py`
- Modify: `api/app/domain/services/codebase/indexer.py`
- Modify: `api/app/infrastructure/repositories/db_codebase_repository.py:200-274`
- Modify: `api/app/domain/repositories/codebase_repository.py`
- Modify: `api/app/domain/services/tools/codebase_tools.py:34-52`
- Test: `api/tests/app/domain/services/codebase/test_lexical_indexer.py`
- Test: `api/tests/app/domain/services/codebase/test_hybrid_retriever.py`

**Interfaces:**

- Produces: `CodeSearchResult(version_id, path, lines, symbol_id, sources, score)`.
- Produces: `HybridCodeRetriever.retrieve(codebase_id, version_id, query, limit)`.
- Consumes: candidate files/symbols/source content.

- [ ] **Step 1: Write lexical and vector-degradation tests**

```python
def test_lexical_document_splits_identifiers(indexer):
    text = indexer.search_text(path="src/user_service.py", symbols=["createUser"], content="def create_user")
    assert {"user", "service", "create", "createuser", "create_user"} <= set(text.split())


@pytest.mark.asyncio
async def test_vector_failure_returns_lexical_results(retriever):
    retriever.vector.embed.side_effect = TimeoutError()
    response = await retriever.retrieve("cb1", "cbv2", "create user", limit=5)
    assert response.items[0].path == "src/user_service.py"
    assert response.capabilities["vector_search"] is False
    assert response.degraded_reasons == ["EMBEDDING_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_hybrid_search_is_version_isolated(retriever):
    response = await retriever.retrieve("cb1", "cbv1", "legacyOnly", limit=10)
    assert {item.version_id for item in response.items} == {"cbv1"}
```

- [ ] **Step 2: Run search tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_lexical_indexer.py tests/app/domain/services/codebase/test_hybrid_retriever.py -q`

Expected: FAIL because semantic_search is vector-only.

- [ ] **Step 3: Implement identifier-aware lexical documents and RRF**

Build `search_text` from path segments, snake/camel identifier tokens, qualified symbol names/signatures, and bounded source text. Save in batches and compute `to_tsvector('simple', search_text)`. Repository exposes:

```python
async def search_lexical(codebase_id, version_id, query, limit) -> list[ScoredChunk]: ...
async def search_vector(codebase_id, version_id, embedding, limit) -> list[ScoredChunk]: ...
```

Fetch `3 * limit` from each available source and fuse with reciprocal rank constant 60. If embedding generation/search fails, return lexical results and structured degradation. Fix `DBCodebaseRepository.save()` to persist the projected active version/degraded fields during compatibility.

- [ ] **Step 4: Run new and existing codebase tool/index tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_lexical_indexer.py tests/app/domain/services/codebase/test_hybrid_retriever.py tests/app/domain/services/tools/test_codebase_tools.py tests/app/domain/services/codebase/test_codebase_ingestion_runner.py tests/app/infrastructure/repositories/test_db_codebase_repository.py -q`

Expected: PASS; empty embedding never means “not found” when lexical matches exist.

- [ ] **Step 5: Commit hybrid retrieval**

```bash
git add api/app/domain/services/codebase/lexical_indexer.py api/app/domain/services/codebase/hybrid_retriever.py api/app/domain/services/codebase/indexer.py api/app/infrastructure/repositories/db_codebase_repository.py api/app/domain/repositories/codebase_repository.py api/app/domain/services/tools/codebase_tools.py api/tests/app/domain/services/codebase api/tests/app/domain/services/tools/test_codebase_tools.py api/tests/app/infrastructure/repositories/test_db_codebase_repository.py
git commit -m "feat(codebase): add lexical fallback and hybrid search"
```

## Task 8: Produce Qualified Symbols and Evidence-Bearing Edges

**Files:**

- Create: `api/app/domain/services/codebase/parsers/base.py`
- Create: `api/app/domain/services/codebase/parsers/python_parser.py`
- Create: `api/app/domain/services/codebase/parsers/tree_sitter_parser.py`
- Create: `api/app/domain/services/codebase/parsers/regex_fallback.py`
- Modify: `api/app/domain/services/codebase/static_analyzer.py`
- Modify: `api/app/domain/models/codebase.py`
- Modify: `api/pyproject.toml`
- Update mechanically: `api/uv.lock`
- Test: `api/tests/app/domain/services/codebase/test_evidence_analyzer.py`

**Interfaces:**

- Produces: `ParsedSymbol(qualified_name, range, parser, confidence)`.
- Produces: `ParsedEdge(kind, resolution, confidence, evidence)`.
- Consumes: source entries from Task 5.

- [ ] **Step 1: Write ambiguity, same-name, and range tests**

```python
def test_same_named_methods_are_not_deduplicated(analyzer):
    result = analyzer.analyze(files={
        "a.ts": "class A { run() { return 1 } }\nclass B { run() { return 2 } }",
    })
    assert {s.qualified_name for s in result.symbols if s.name == "run"} == {"A.run", "B.run"}


def test_ambiguous_call_is_not_bound_to_first_symbol(analyzer):
    result = analyzer.analyze(files={
        "a.py": "def work(): pass",
        "b.py": "def work(): pass",
        "c.py": "def caller(): work()",
    })
    edge = next(e for e in result.edges if e.callee_name == "work")
    assert edge.dst_symbol_id is None
    assert edge.resolution == "ambiguous"


def test_non_python_symbol_range_contains_body(analyzer):
    symbol = analyzer.analyze(files={"a.ts": "function f() {\n  return 1\n}\n"}).symbols[0]
    assert (symbol.start_line, symbol.end_line) == (1, 3)
```

- [ ] **Step 2: Add the parser dependency and run failing tests**

Run: `cd api && uv add tree-sitter-language-pack`

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_evidence_analyzer.py -q`

Expected: tests FAIL against the current regex/name-first analyzer.

- [ ] **Step 3: Implement parser adapters and conservative resolution**

Python parser records module/class/function qualified scopes and imports. Tree-sitter parser covers JavaScript/TypeScript, Java, Go, Rust, C/C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Vue, SQL, and Shell when a grammar is available. Regex fallback remains for unsupported text and sets `parser="regex"`, `confidence=0.3`.

Resolve calls in order: local lexical scope, explicit import/alias, same module, unique project-wide symbol. More than one candidate produces `resolution="ambiguous"` and no `dst_symbol_id`. Preserve exact evidence location for every edge. Emit only edge kinds actually observed.

- [ ] **Step 4: Run analyzer, indexer, and artifact baseline tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_evidence_analyzer.py tests/app/domain/services/codebase/test_static_analyzer.py tests/app/domain/services/codebase/test_codebase_ingestion_runner.py -q`

Expected: PASS; update old expectations that assumed one-line regex ranges or first-name binding.

- [ ] **Step 5: Commit evidence analyzers**

```bash
git add api/app/domain/services/codebase/parsers api/app/domain/services/codebase/static_analyzer.py api/app/domain/models/codebase.py api/pyproject.toml api/uv.lock api/tests/app/domain/services/codebase
git commit -m "feat(codebase): analyze qualified symbols with evidence"
```

## Task 9: Generate Only Evidence-Supported Artifacts

**Files:**

- Modify: `api/app/domain/services/codebase/artifact_generator.py`
- Modify: `api/app/domain/models/codebase.py`
- Modify: `api/app/domain/services/tools/codebase_tools.py:204-220`
- Test: `api/tests/app/domain/services/codebase/test_evidence_artifact_generator.py`
- Test: `api/tests/app/domain/services/codebase/test_artifact_generator.py`

**Interfaces:**

- Consumes: Task 8 symbols/edges/evidence.
- Produces: `ArtifactGenerationResult(artifacts, unsupported_views)`.
- Produces: every graph edge `meta.evidence_refs`.

- [ ] **Step 1: Write no-fabrication and evidence tests**

```python
def test_empty_evidence_does_not_generate_architecture_dataflow_or_flowchart(generator):
    result = generator.generate_all("cbv1", files=[], symbols=[], edges=[], language_stats={})
    assert {a.kind for a in result.artifacts} == set()
    assert result.unsupported_views == {
        ArtifactKind.ARCHITECTURE: "insufficient_evidence",
        ArtifactKind.DATA_FLOW: "unsupported",
        ArtifactKind.CALL_CHAIN: "insufficient_evidence",
        ArtifactKind.FLOWCHART: "unsupported",
    }


def test_call_chain_edges_have_source_evidence(generator, evidence_analysis):
    artifact = generator.generate_call_chain(evidence_analysis)
    assert artifact.content
    assert artifact.meta["edges"]
    assert all(edge["evidence_refs"] for edge in artifact.meta["edges"])


def test_function_list_is_never_serialized_as_flow(generator, evidence_analysis):
    result = generator.generate_all_from_analysis(evidence_analysis)
    assert ArtifactKind.FLOWCHART not in {a.kind for a in result.artifacts}
```

- [ ] **Step 2: Run artifact tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_evidence_artifact_generator.py tests/app/domain/services/codebase/test_artifact_generator.py -q`

Expected: FAIL because architecture/data flow are fixed templates and flowchart chains function order.

- [ ] **Step 3: Replace templates with evidence projections**

Always permit:

- overview from measured counts with file/symbol refs;
- module directory from actual paths.

Conditionally permit:

- architecture from resolved IMPORT/DEPENDENCY edges grouped by real modules;
- call chain from resolved/ambiguous CALL edges with confidence;
- data flow only from explicit DATA_FLOW edges;
- flowchart only from control-flow edges produced by a supported parser.

Omit unsupported artifacts and return their reason in version capabilities. Store `version_id`, file path, line range, symbol ID, analyzer, and confidence for every node/edge.

- [ ] **Step 4: Run artifact, tool, and version validation tests**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/codebase/test_evidence_artifact_generator.py tests/app/domain/services/codebase/test_artifact_generator.py tests/app/domain/services/tools/test_codebase_tools.py tests/app/domain/services/codebase/test_versioned_ingestion_runner.py -q`

Expected: PASS; no generic UI→API or sequential-function diagram remains.

- [ ] **Step 5: Commit evidence artifacts**

```bash
git add api/app/domain/services/codebase/artifact_generator.py api/app/domain/models/codebase.py api/app/domain/services/tools/codebase_tools.py api/tests/app/domain/services/codebase/test_evidence_artifact_generator.py api/tests/app/domain/services/codebase/test_artifact_generator.py api/tests/app/domain/services/tools/test_codebase_tools.py
git commit -m "feat(codebase): generate only evidence backed artifacts"
```

## Task 10: Expose Versioned Codebase APIs and UX

**Files:**

- Modify: `api/app/interfaces/schemas/codebase.py`
- Modify: `api/app/interfaces/endpoints/codebase_routes.py`
- Modify: `ui/src/lib/api/codebase.ts`
- Modify: `ui/src/lib/api/types.ts`
- Create: `ui/src/components/codebase/codebase-version-status.tsx`
- Create: `ui/src/components/codebase/code-evidence-panel.tsx`
- Modify: `ui/src/components/codebase/codebase-library.tsx`
- Modify: `ui/src/components/codebase/codebase-detail-redirect.tsx`
- Test: `api/tests/app/interfaces/endpoints/test_codebase_version_routes.py`
- Test: `ui/src/components/codebase/codebase-version-status.test.tsx`
- Test: `ui/src/components/codebase/codebase-library.test.tsx`

**Interfaces:**

- Consumes: CodebaseVersionService, shared build events, binding endpoints.
- Produces: version/build/evidence UI.

- [ ] **Step 1: Write API and UI behavior tests**

```python
def test_duplicate_reanalyze_returns_existing_build(client, running_build):
    response = client.post(f"/api/codebases/{running_build.resource_id}/builds", json={"kind": "reanalyze"})
    assert response.status_code == 200
    assert response.json()["data"]["build_id"] == running_build.id
```

```tsx
it("keeps Ask and Agent enabled while a candidate rebuilds", async () => {
  render(<CodebaseLibrary api={activeV1WithBuildingV2} />);
  expect(screen.getByRole("button", { name: "开始问答" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "开始 Agent" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "查看构建" })).toBeEnabled();
});

it("creates a session with the displayed active version", async () => {
  render(<CodebaseLibrary api={readyV2} />);
  await user.click(screen.getByRole("button", { name: "开始 Agent" }));
  expect(sessionApi.createSession).toHaveBeenCalledWith(
    expect.objectContaining({ codebase_id: "cb1", codebase_version_id: "cbv2", mode: "agent" }),
  );
});
```

- [ ] **Step 2: Run API and UI tests**

Run: `cd api && .venv/bin/pytest tests/app/interfaces/endpoints/test_codebase_version_routes.py -q`

Run: `cd ui && npm test -- --run src/components/codebase/codebase-version-status.test.tsx src/components/codebase/codebase-library.test.tsx`

Expected: FAIL because build/version contracts and status UI are absent.

- [ ] **Step 3: Implement explicit version/build/evidence endpoints**

Add:

```text
GET  /codebases/{id}/versions
GET  /codebases/{id}/versions/{version_id}
POST /codebases/{id}/builds
POST /codebases/{id}/builds/{build_id}/retry
POST /codebases/{id}/builds/{build_id}/cancel
POST /codebases/{id}/versions/{version_id}/source
GET  /codebases/{id}/versions/{version_id}/artifacts
```

Keep `/reanalyze` as a compatibility adapter to `POST /builds`. Make `/download` read an existing version snapshot and never mutate DB. UI reanalyze becomes “查看构建” when one is active. Show active/candidate versions, degraded reasons, unsupported artifact reasons, source-linked evidence, and context-upgrade action.

- [ ] **Step 4: Run endpoint, library, redirect, and evidence UI tests**

Run: `cd api && .venv/bin/pytest tests/app/interfaces/endpoints/test_codebase_routes.py tests/app/interfaces/endpoints/test_codebase_version_routes.py -q`

Run: `cd ui && npm test -- --run src/components/codebase`

Expected: PASS; sessions always send an explicit displayed version.

- [ ] **Step 5: Commit versioned codebase UX**

```bash
git add api/app/interfaces/schemas/codebase.py api/app/interfaces/endpoints/codebase_routes.py ui/src/lib/api/codebase.ts ui/src/lib/api/types.ts ui/src/components/codebase
git commit -m "feat(codebase): expose versioned build and evidence ux"
```

## Task 11: Extend Safe Version GC to Codebase Snapshots and Analysis

**Files:**

- Modify: `api/app/application/services/resource_version_gc_service.py`
- Modify: `api/app/infrastructure/repositories/db_codebase_version_repository.py`
- Modify: `api/app/infrastructure/external/scheduler/job_scheduler.py`
- Modify: `api/config.yaml`
- Test: `api/tests/app/application/services/test_codebase_version_gc.py`

**Interfaces:**

- Consumes: active versions, session bindings, build references, content-addressed snapshot refs.
- Produces: `collect_codebase_versions(retain_count, min_age_days, batch_size)`.

- [ ] **Step 1: Write binding and shared-snapshot retention tests**

```python
@pytest.mark.asyncio
async def test_gc_keeps_bound_version_and_shared_snapshot(gc, versions, storage):
    deleted = await gc.collect_codebase_versions(retain_count=2, min_age_days=30, batch_size=25)
    assert versions.bound_old.id not in deleted
    assert versions.expired_unbound.id in deleted
    assert await storage.exists(versions.shared_snapshot_key)


@pytest.mark.asyncio
async def test_last_snapshot_reference_is_deleted(gc, only_expired_version, storage):
    await gc.collect_codebase_versions(retain_count=0, min_age_days=0, batch_size=25)
    assert not await storage.exists(only_expired_version.source_snapshot_key)
```

- [ ] **Step 2: Run Codebase GC tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_codebase_version_gc.py -q`

Expected: FAIL because only logical codebase deletion cleans resources.

- [ ] **Step 3: Implement reference-counted analysis and snapshot cleanup**

Use the same eligibility rules as KB GC. Delete candidate/version-scoped files, symbols, edges, chunks, and artifacts in a transaction. Delete an object-storage snapshot only after confirming no other version references its key. Limit work by `codebase.version_gc_batch_size` and record deleted row/byte counts.

- [ ] **Step 4: Run both domain GC and binding tests**

Run: `cd api && .venv/bin/pytest tests/app/application/services/test_codebase_version_gc.py tests/app/application/services/test_knowledge_version_gc.py tests/app/application/services/test_resource_binding_service.py -q`

Expected: PASS; shared snapshots remain until their last version reference is gone.

- [ ] **Step 5: Commit Codebase retention**

```bash
git add api/app/application/services/resource_version_gc_service.py api/app/infrastructure/repositories/db_codebase_version_repository.py api/app/infrastructure/external/scheduler/job_scheduler.py api/config.yaml api/tests/app/application/services/test_codebase_version_gc.py
git commit -m "feat(codebase): garbage collect unbound analysis versions"
```

## Task 12: Verify Codebase Invariants and Update Documentation

**Files:**

- Create: `api/tests/app/contracts/test_codebase_version_invariants.py`
- Modify: `docs/architecture/codebase-reindex.zh-CN.md`
- Modify: `docs/architecture/codebase-reindex.md`
- Modify: `docs/architecture/security-model.zh-CN.md`
- Modify: `docs/architecture/security-model.md`

**Interfaces:**

- Consumes: all Tasks 1-11.
- Produces: executable Codebase acceptance gate and authoritative documentation.

- [ ] **Step 1: Add security, atomicity, fallback, history, and evidence contracts**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [
    "materialize", "snapshot", "analyze", "keyword_index",
    "vector_index", "artifacts", "validate", "publish",
])
async def test_every_build_failure_preserves_active_codebase(system, phase):
    before = await system.search("cb1", "cbv1", "known symbol")
    outcome = await system.reanalyze("cb1", fail_at=phase)
    after = await system.search("cb1", "cbv1", "known symbol")
    if phase in {"vector_index", "artifacts"}:
        assert outcome.state == BuildState.DEGRADED
    else:
        assert outcome.state == BuildState.FAILED
    assert after == before


@pytest.mark.asyncio
async def test_old_session_reads_old_snapshot_after_new_publish(system):
    binding = await system.bind("s1", "cb1", "cbv1")
    await system.publish_source("cb1", {"main.py": "print('v2')"})
    assert "v1" in await system.read(binding, "main.py")


@pytest.mark.asyncio
async def test_vector_outage_keeps_lexical_search_and_marks_degraded(system):
    response = await system.search_with_embedding_failure("cb1", "cbv2", "main")
    assert response.items
    assert "EMBEDDING_UNAVAILABLE" in response.degraded_reasons


def test_every_artifact_fact_has_evidence(system):
    for artifact in system.artifacts("cbv2"):
        for fact in artifact.facts:
            assert fact.evidence_refs
```

Include malicious Git/ZIP/path inputs and concurrent reanalyze in this contract suite.

- [ ] **Step 2: Run Codebase contract tests**

Run: `cd api && .venv/bin/pytest tests/app/contracts/test_codebase_version_invariants.py -q`

Expected: PASS; fix the owning task for any failure.

- [ ] **Step 3: Rewrite authoritative Codebase and security docs**

Document:

- candidate build/version state machine and CAS publish;
- clean source acquisition, HTTPS Git restrictions, ZIP limits, immutable snapshots;
- session binding, Agent workspace copy, local-edit upgrade conflict;
- lexical/vector hybrid retrieval and degradation;
- parser/resolution/confidence contract;
- EvidenceRef and unsupported diagram behavior;
- build recovery, compatibility, and GC.

Remove statements that degraded vector search returns empty, reanalysis clears current analysis, or generic diagrams are always generated.

- [ ] **Step 4: Run complete three-line verification**

Run: `cd api && .venv/bin/pytest tests/app/domain/services/flows tests/app/domain/services/agents tests/app/domain/services/knowledge_base tests/app/domain/services/codebase tests/app/domain/services/tools tests/app/application/services/test_resource_binding_service.py tests/app/application/services/test_resource_guard_service.py tests/app/application/services/test_knowledge_version_service.py tests/app/application/services/test_codebase_version_service.py tests/app/contracts -q`

Run: `cd ui && npm test -- --run src/lib/session-events.test.ts src/components/knowledge src/components/codebase src/components/workspace/knowledge-context-panel.test.tsx`

Run: `cd api && .venv/bin/alembic heads`

Expected: all tests PASS and Alembic prints exactly `d8e9f0a1b2c3 (head)`.

- [ ] **Step 5: Commit final verification and docs**

```bash
git add api/tests/app/contracts/test_codebase_version_invariants.py docs/architecture/codebase-reindex.zh-CN.md docs/architecture/codebase-reindex.md docs/architecture/security-model.zh-CN.md docs/architecture/security-model.md
git commit -m "docs: document versioned evidence based code analysis"
```

## Completion Gate

The codebase line is complete only when:

- invalid/unsafe source input is rejected before task creation;
- every build uses a clean workspace and immutable snapshot;
- concurrent reanalysis is idempotent and any core failure leaves active analysis unchanged;
- Ask and Agent source reads use their bound version after ingestion sandboxes are destroyed;
- vector failure returns lexical results with a visible degraded reason;
- same-name symbols are preserved and ambiguous calls are not falsely resolved;
- every generated graph fact has source evidence and unsupported views are absent;
- old bound sessions remain reproducible after new versions publish;
- migration `d8e9f0a1b2c3` is the sole Alembic head.
