# Technical Decisions

[简体中文](technical-decisions.zh-CN.md)

This document records the current architecture, not an upgrade path.

## 1. Modular Python backend

FastAPI, Pydantic, and SQLAlchemy sit behind explicit
interfaces/application/domain/infrastructure boundaries. Manual typed
composition builds `ApiRuntime` and `KernelRuntime` independently; imports do
not allocate resources, HTTP dependencies resolve from `app.state`, and each
process loads deployment settings once. Import contracts keep application code
independent from infrastructure and prevent service-locator/global-getter
patterns from returning.

## 2. Explicit transactions and post-commit hints

A unit of work always rolls back when its context exits without an explicit
`uow.commit()`, even on a normal return. Application mutation methods own that
commit decision; repositories never commit. Redis publication happens only as
a post-commit hint, so Redis failure can increase latency but cannot alter the
authoritative PostgreSQL result.

## 3. PostgreSQL event-sourced execution

Every Agent, Ask, resource build, automation, patrol, and remediation action is
a typed Run. Append-only, hash-chained execution events are the only lifecycle
facts. Command inbox idempotency, durable Activities, timers, outbox delivery,
integrity-checked snapshots, and rebuildable projections are implemented in
the same PostgreSQL database.

This keeps self-hosting operationally small while making process death,
duplicate delivery, reconnects, and Redis loss explicit recovery cases.

## 4. Redis is transport, not state

Redis supplies wake-up hints, caches, and circuit-breaker coordination. The
kernel always polls PostgreSQL pending rows. No Run status, Activity outcome,
approval, or cursor is authoritative in Redis.

## 5. Durable Activity boundary

LLM, retrieval, parser/indexer, sandbox, browser, MCP, A2A, storage, and
actuator work is nondeterministic and therefore runs as an Activity. Invocation
intent and call-start are durable before provider access. Generation fencing
rejects stale completions, and unknown non-idempotent outcomes wait for an
explicit operator resolution.

## 6. Immutable resource publication

Knowledge and codebase ingestion build immutable candidates. Publication
validates the complete closure and compare-and-swaps the active version.
Sessions bind a concrete published version, so later builds cannot change the
evidence boundary of an existing Run.

## 7. Forced tenant isolation and least privilege

Owner-scoped tables use application filters plus forced PostgreSQL RLS. The
API, execution kernel, migration, and bootstrap roles are distinct. Runtime
roles do not own the schema. The event store additionally freezes OwnerScope at
stream creation and rejects mismatched appends.

## 8. Sandboxed tools and narrow operations plane

Browser, shell, and file tools run in a Docker or Kubernetes sandbox with
resource caps and controlled egress. A broker owns Docker access in Compose.
Ops Collector is read-only; Ops Actuator exposes a closed set of mutations and
is reachable only through policy-checked, persisted approvals.

## 9. Shared object storage

Artifacts, attachments, large Activity inputs/results, snapshots of source
material, and evidence packages use shared object storage. Production supports
COS or S3-compatible MinIO. Database rows store references and digests rather
than unbounded provider payloads.

## 10. Next.js projection client

The Next.js UI submits commands and renders formal projections. SSE live and
replay use one public event contract. Browser state never decides a Run
transition; approval actions target persisted approval batches. Authenticated
resource caches are provider-owned and scoped by `userId + workspaceId`, with
generation invalidation preventing late responses from crossing an identity
or workspace boundary.

## 11. Versioned secret envelopes

LLM and integration secrets use `v2.<key-id>...` Fernet envelopes. An active
key writes new values and an explicit previous-key ring supports planned key
rotation. Audit signatures have a separate key ring. Plaintext credentials are
never a supported persistence format.

## 12. One greenfield schema

Alembic contains one initial revision for the current catalog. The project
does not ship execution history conversion, alternate event schemas, or
runtime routing between engines. Structural changes update the greenfield
schema until the first supported production release establishes an upgrade
contract.
