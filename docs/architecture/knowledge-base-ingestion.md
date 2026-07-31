# Knowledge Base Versioned Ingestion

OpenCitadel treats a knowledge base as a stable logical resource whose readable
content is an immutable, published version. A rebuild never edits the active
index in place. It creates a candidate closure, validates it, and atomically
moves `knowledge_bases.active_version_id` only after the closure is safe to
read.

This is the authoritative model for new code. The unversioned document-content
routes remain compatibility surfaces for older clients; retrieval, citations,
source expansion, GraphRAG, Ask, and Agent execution must use an explicit
published `version_id`.

## Identity and storage model

| Concept | Identity and role |
| --- | --- |
| Knowledge base | Stable owner-scoped resource and pointer to the active published version |
| Knowledge-base version | Immutable candidate or published snapshot; records parent, build, capabilities, degradation reasons, metrics, and publication time |
| Logical document | Stable document metadata within a knowledge base |
| Document revision | Immutable source digest and processing state for one document payload |
| Version manifest | Ordered mapping from a version to exact `(document_id, document_revision_id)` pairs |
| Chunk and graph rows | Derived data carrying both `kb_id` and `version_id`; never shared by inference across versions |
| Resource build | Durable queued/running/terminal operation associated with one candidate version |
| Session binding | Immutable record pinning an Ask or Agent session to one concrete published version |

A version is a readable closure only when every manifest entry resolves to its
exact revision and mandatory derived rows. Parent/child chunks, keyword rows,
vector rows, entities, relations, and evidence references are always filtered
by the bound version.

## State machines

Document revisions move through:

```text
uploaded -> parsing -> parsed -> indexing -> indexed
                    \              \-> failed
                     \-> failed
```

`parsed` means that source extraction succeeded. It does **not** mean that the
document can be retrieved or used to create a session. Only an indexed
revision inside a published closure is readable by production Q&A.

Knowledge-base versions move through:

```text
building -> ready
         -> degraded
         -> failed
```

`ready` and `degraded` are published terminal states. `degraded` is truthful:
mandatory keyword retrieval and source reading work, while one or more optional
capabilities are disabled and the reason is exposed by the version/build status
surface.

Resource builds move through durable `queued` and `running` states to exactly
one of `succeeded`, `degraded`, `failed`, or `cancelled`. A cancellation request
does not manufacture a terminal state in the HTTP request; the worker observes
the request, checkpoints safely, and owns terminalization.

## Candidate build pipeline

Every add, removal, reindex, or retry operates on a candidate:

1. Lock the knowledge-base mutation boundary and create one durable build and
   one `building` version whose parent is the current active version.
2. Copy the parent manifest, then apply the requested additions or removals.
   Unchanged revisions are reused by identity; changed source bytes produce new
   immutable revisions.
3. Parse changed revisions and persist extraction state.
4. Build hierarchical parent/child chunks.
5. Build the mandatory keyword index.
6. Optionally build embeddings and the graph, each within explicit budgets.
7. Validate the complete candidate closure and its identity constraints.
8. In one transaction, compare the expected parent with the current active
   pointer, finalize version/build status, and publish by swapping
   `active_version_id`.

The compare-and-swap at publication prevents a stale concurrent candidate from
overwriting a newer active version. A failed dispatch leaves the durable queued
build available to worker reconciliation; it does not create a duplicate.

## Failure semantics

| Failure point | Candidate/build result | Active version |
| --- | --- | --- |
| Parse | Failed | Unchanged and readable |
| Chunking | Failed | Unchanged and readable |
| Keyword indexing | Failed | Unchanged and readable |
| Closure validation | Failed | Unchanged and readable |
| Publication CAS or commit | Failed | Unchanged and readable |
| Vector indexing | Published `degraded`; `vector_search=false` | Atomically advances |
| Graph extraction, budget, or deadline | Published `degraded`; `graph_search=false` | Atomically advances |

Mandatory failures never clear active chunks and never create a retrieval
blackout. Optional failures never pretend that a capability exists. Graph rows
from an incomplete attempt are not exposed as a half-finished graph.

The current reason codes include `DOCUMENT_PARTIAL`,
`EMBEDDING_UNAVAILABLE`, and GraphRAG failure/budget reasons. Read them from
version or build status. The Graph endpoint itself returns
`capability=false` with empty nodes and edges when graph search is unavailable.

## Retrieval and session consistency

Both Ask and Agent session creation resolve a published version, recheck it at
the final transaction boundary, and persist exactly one knowledge-base binding
with the session. `ready_doc_count` is a display/compatibility count, not an
authorization predicate.

The runner authorizes the persisted binding again and passes the same
`version_id` to the retriever and knowledge tools. Missing, duplicated,
foreign-resource, `building`, `failed`, or unpublished bindings fail closed.
Publishing a newer version does not silently change an existing session.

An explicit session upgrade creates a replacement current binding and keeps the
superseded binding as history. This preserves auditability and makes old event
logs reproducible.

## Citations and source expansion

Every retrieval result and graph evidence reference carries the complete
identity:

```text
(version_id, document_revision_id, doc_id, page_no, chunk_id)
```

`page_no` may be absent for source formats without page metadata; the other
identities still resolve the exact version closure. Source expansion uses:

```text
GET /knowledge-bases/{kb_id}/versions/{version_id}/documents/{doc_id}/content
```

The endpoint returns the resolved `document_revision_id`, ordered content
items, `next_cursor`, `total`, and `truncated`. Cursors are bound to the
knowledge base, version, document, revision, and page filter. They must not be
reused after changing any of those fields. The versioned endpoint is the
authoritative source viewer for citations.

## GraphRAG

Graph extraction produces entity nodes, relation edges, and evidence references
to real chunks. The API is:

```text
GET /knowledge-bases/{kb_id}/versions/{version_id}/graph
```

It accepts `q`, `cursor`, and `limit` and returns real entity endpoints; document
placeholder nodes are not synthesized. Edge endpoints must exist in the
returned node set, and evidence uses the same five-part citation identity.

Graph construction is bounded by `max_parent_chunks_per_doc`, `max_chunks`,
`max_llm_calls`, `max_tokens`, concurrency, and a durable deadline. Checkpoints
store the candidate version, cursor, accumulated call/token counts, and deadline
so retries cannot reset the budget. A cap, deadline, or extraction failure
degrades graph capability rather than blocking mandatory keyword publication.

## Commands and operational recovery

The owner-scoped API exposes:

```text
GET  /knowledge-bases/{kb_id}/versions
GET  /knowledge-bases/{kb_id}/versions/{version_id}
POST /knowledge-bases/{kb_id}/builds
POST /knowledge-bases/{kb_id}/builds/{build_id}/retry
POST /knowledge-bases/{kb_id}/builds/{build_id}/cancel
POST /knowledge-bases/{kb_id}/reindex
```

Only one active candidate may mutate a knowledge base at a time. Exact duplicate
commands are idempotent. Retry creates a new candidate from the failed
candidate's immutable manifest; it does not revive or overwrite that version.
Reindex builds a new candidate from the active manifest and never calls an
in-place `clear_index_data`.

Removing a document edits the next candidate manifest. It does not synchronously
delete the logical document, revision, chunks, graph evidence, or older
versions. The active pointer changes only when the removal candidate publishes.

Worker reconciliation detects durable queued builds that were not dispatched
and stale running builds whose lease/heartbeat is no longer valid. Recovery
resumes from durable state or terminalizes the candidate without changing the
active version.

## Retention and garbage collection

Version GC is opt-in:

```yaml
knowledge_base:
  version_gc_enabled: false
  version_retention_count: 10
  version_retention_min_days: 30
  version_gc_batch_size: 50
```

The scheduler runs bounded GC under a leader lease. Active versions, candidates
referenced by nonterminal builds, and **every version referenced by any session
binding, including `is_current=false` history**, are permanent roots for that
collection pass. Parent pointers are GC-safe, and deletion order preserves
foreign keys across graph rows, chunks, manifests, revisions, and logical
documents. Shared revisions/documents are reclaimed only when no retained
version references them. GC reports protected counts and reclaimed rows/bytes.

Enable GC only after observing a dry operational window and choosing retention
values that match audit requirements.

## Migration and compatibility

The versioned schema is a linear Alembic chain:

```text
b8d9e0f1a2b3 -> c7d8e9f0a1b2 -> d8e9f0a1b2c3 (head)
```

`b8d9e0f1a2b3` marks resources that were already ready at the compatibility
boundary. `c7d8e9f0a1b2` expands the schema, backfills immutable legacy-v1
versions/revisions/manifests, and adds version identity to derived data.
`d8e9f0a1b2c3` makes the version parent relationship and GC lookup indexes safe.
The current and only head is `d8e9f0a1b2c3`, whose parent is
`c7d8e9f0a1b2`.

Legacy readiness and unversioned source routes remain for compatibility, but
new writes must provide candidate version identity and new production reads
must follow a published session/version binding.
