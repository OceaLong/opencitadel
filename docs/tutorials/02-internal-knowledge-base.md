[简体中文](02-internal-knowledge-base.zh-CN.md)

# Build and Use an Internal Knowledge Base

This tutorial creates a versioned knowledge base, follows its build, starts
version-pinned Ask and Agent sessions, and safely updates the content without a
retrieval outage.

## 1. Create the logical knowledge base

Open **Knowledge** in the intended workspace and create a library with a durable
name such as “Engineering Handbook.” New installations use text extraction and
keyword search. Uploading and indexing text documents needs no inference model;
Ask and Agent answers still require a configured chat model.

Chunking and retrieval behavior follow the current system Execution Policy,
managed by an administrator under **Settings → Runtime policy**. Upgrades retain
existing policy revisions and running tasks; the new installation defaults do
not overwrite them.

To enable optional capabilities, configure their models under **Settings →
Inference**, then enable the corresponding Knowledge Base fields in Execution
Policy:

| Capability | Required configuration | When it takes effect |
| ---------- | ---------------------- | -------------------- |
| Vector search | 1536-dimensional embedding model and `embedding` binding; `vector_enabled=true` | New builds; rebuild existing libraries to add vectors |
| Reranking | Ask/Agent's selected chat model, or the effective `chat` binding when no model is selected; `rerank.enabled=true` | New Ask/Agent Runs |
| GraphRAG | Effective `chat` binding; `graphrag.enabled=true` | New builds; rebuild existing libraries to add graph data |
| Scanned PDF OCR | Vision-capable chat model and `chat` binding; `ocr.mode=vision_llm` | Parsing new or previously failed document revisions |

OCR is off initially. Use PDFs with extractable text or enable OCR before
importing scans. Policy changes do not alter published versions or in-flight
Runs. Previously extracted document text is reused during rebuilds.

Knowledge-base reranking currently uses the task's chat model; it does not
consume a separate `rerank` binding.

At this point the knowledge base may have no active version. A document count or
`ready_doc_count` alone is not enough to start production Q&A; a published
`ready` or `degraded` version is required.

## 2. Add immutable sources

Upload supported files or add approved web, Confluence, or Feishu URLs. The
server downloads each source, records a digest, and creates an immutable
document revision. Adding content creates a candidate version and a durable
build; it does not mutate the current active version.

The candidate manifest reuses unchanged revisions and contains exact
`(document_id, document_revision_id)` pairs. If a source changes, it receives a
new revision instead of overwriting the old bytes.

## 3. Follow the build

The library view shows the active version and active candidate. You can also
inspect:

```text
GET /api/knowledge-bases/{kb_id}/versions
GET /api/knowledge-bases/{kb_id}/versions/{version_id}
```

The pipeline reports parse, chunk, keyword-index, vector, graph, validate, and
publish progress. Interpret status as follows:

| Status     | Meaning                                                                                         |
| ---------- | ----------------------------------------------------------------------------------------------- |
| `building` | Candidate is incomplete and cannot be used by Ask or Agent                                      |
| `ready`    | Published with all configured capabilities                                                      |
| `degraded` | Published; indexed documents remain searchable, with failed documents or optional stages reported |
| `failed`   | Candidate was not published; the previous active version remains readable                       |

A revision at `parsed` is still not searchable. Wait for the candidate version
to publish; do not use `ready_doc_count` as a shortcut.

A successful basic build is `ready` with `keyword_search=true`,
`vector_search=false`, and `graph_search=false`. Intentionally disabled
capabilities do not make a build degraded.

## 4. Handle degraded builds

A partial document parse failure or an enabled vector/GraphRAG outage may
produce a truthful `degraded` publication.
Inspect the version/build capabilities and `degraded_reasons`:

- `vector_search=false` means retrieval continues with keyword search.
- `graph_search=false` means graph exploration is unavailable and returns no
  partial graph.
- `DOCUMENT_PARTIAL` means some documents failed to parse while successfully
  indexed documents remain searchable.
- If all documents fail to parse, or a mandatory chunk, keyword, validation, or
  publish stage fails, the candidate fails and the active version is unchanged.

The Graph endpoint itself expresses unavailability as `capability=false` with
empty nodes and edges. The detailed reason remains on the version/build status
surface.

## 5. Start a pinned Ask or Agent session

From the knowledge library choose **Ask** for focused Q&A or **Agent** for a
tool-using workflow. Both modes resolve one concrete published version and save
that binding atomically with the session.

You may choose a published historical version when the UI or API exposes a
version selector. Otherwise the current active published version is selected.
The selected version is shown in session context.

Publishing a later version does not change an existing session. Missing,
foreign, duplicated, building, failed, or unpublished bindings are rejected by
the runner.

## 6. Verify citations and read the exact source

Answers cite the precise indexed evidence:

```text
(version_id, document_revision_id, doc_id, page_no, chunk_id)
```

`page_no` can be empty for sources without page metadata. Opening a citation
uses the versioned source endpoint:

```text
GET /api/knowledge-bases/{kb_id}/versions/{version_id}/documents/{doc_id}/content
```

Use either a page filter or the returned `next_cursor` to continue. The response
also includes `document_revision_id`, ordered items, `total`, and `truncated`.
Do not reuse a cursor with another knowledge base, version, document, revision,
or page filter.

This is why an old session can still open exactly the source it cited after a
new version is published.

## 7. Explore the knowledge graph

For a published version with `graph_search=true`, open the Graph panel or call:

```text
GET /api/knowledge-bases/{kb_id}/versions/{version_id}/graph?q=term&limit=50
```

Pass the returned `next_cursor` as `cursor` when present. Nodes are extracted entities, edges connect
real returned entity endpoints, and edge evidence links back to exact source
chunks. If `capability=false`, use keyword/vector search rather than treating
the empty graph as “no relationships found.”

Graph processing has chunk, LLM-call, token, concurrency, and deadline budgets.
Crossing a budget degrades graph capability without blocking keyword
publication.

## 8. Update, reindex, or remove content

All mutations create a candidate:

- **Add** appends new revisions to a copy-on-write manifest.
- **Reindex** rebuilds from the active manifest without clearing active data.
- **Remove** excludes the document from the next manifest; it does not
  synchronously erase historical evidence.

The old active version stays queryable until the candidate passes validation and
publishes atomically. If the candidate fails, existing Ask/Agent sessions and
new sessions using the old active version continue to work.

Only one active candidate is allowed per knowledge base. Repeating the exact
same command is idempotent.

## 9. Retry, cancel, and recover

For a failed build, choose **Retry** or call:

```text
POST /api/knowledge-bases/{kb_id}/builds/{build_id}/retry
```

Retry creates a new candidate from the failed candidate's immutable manifest.
The failed version remains part of audit history.

For an active queued/running build, choose **Cancel** or call:

```text
POST /api/knowledge-bases/{kb_id}/builds/{build_id}/cancel
```

The request records cancellation in the Run and stops at an Activity boundary.
Continue watching build status until it becomes terminal. If an Activity claim
expires, the execution kernel safely resumes the durable build or marks it
failed without changing the active version.

## 10. Upgrade a session deliberately

When a newer version is published, an existing session keeps its original
binding. Use the session context’s upgrade action to create a replacement
current binding. The earlier binding remains historical (`is_current=false`) so
past events and citations remain reproducible.

Upgrade only when you want future turns to use the newer snapshot. For an
investigation or regulated workflow, keeping the old version may be the correct
choice.

## 11. Retention guidance

Version garbage collection is disabled by default. When enabled, it respects
retention count, minimum age, and batch size. Active versions, nonterminal build
candidates, and every version referenced by current **or historical** session
bindings are protected.

Before enabling GC:

1. Decide the audit retention policy.
2. Confirm old citations and source paging work.
3. Observe build recovery and GC metrics.
4. Enable bounded collection with conservative count and age settings.

Removing a document from the current version is therefore not immediate
physical deletion. Unreferenced rows are reclaimed only after retention and
binding safety rules permit it.

## Operational checklist

- Start Q&A only from a published `ready` or `degraded` version.
- Treat capabilities and degradation reasons as truth, not just the top-level
  knowledge-base status.
- Use versioned citations and source routes.
- Expect rebuilds and removals to preserve the active version until publish.
- Retry or cancel via the durable build identity.
- Upgrade sessions explicitly.
- Keep GC off until retention and audit requirements are understood.
