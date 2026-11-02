# Configuration Sources and Governance

[简体中文](config-source-governance.zh-CN.md)

Every value has exactly one authority.

| Kind                                  | Authority                         | Examples                                                                                                                                                                                                                                                                           |
| ------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deployment topology and secrets       | Environment or secret manager     | database identities, signing/encryption keys (`API_KEY_SECRET`, `AUDIT_SIGNING_KEY`, `JWT_SECRET`/`JWT_PREVIOUS_SECRETS`, `DATABASE_AUTHORIZATION_SIGNING_SECRET`, `SANDBOX_TOKEN_SEED`, `OPS_ACTUATOR_TOKEN`/`OPS_COLLECTOR_TOKEN`), OAuth, storage, sandbox driver/image/network |
| Live domain behavior                  | PostgreSQL Runtime Policy head    | agent limits, timeouts, retries, scheduler, sandbox limits, knowledge/Patrol retention                                                                                                                                                                                             |
| Integrations                          | Owner-scoped PostgreSQL resources | inference endpoints/models/bindings, MCP, A2A, Skills                                                                                                                                                                                                                              |
| Product data                          | Domain tables                     | sessions, jobs, Packs, resources, versions                                                                                                                                                                                                                                         |
| Process safety and maintenance limits | Deployment Settings               | execution capacity/backpressure, poison-claim ceilings, queue and recycle-bin retention, evaluation environment capacity                                                                                                                                                           |

There is no runtime YAML overlay, per-user behavior override, or environment
fallback for policy fields. Migration creates a typed Execution Policy revision,
a typed Operations Policy revision, and one atomic head that activates both.
Later changes are immutable revisions created through the admin API/UI.

New installations seed basic knowledge processing: text extraction and keyword
search, with vector search, reranking, GraphRAG, and OCR off. Optional inference
bindings and policy switches enable enhancements later. Existing policy heads
and frozen Runs keep their stored settings during upgrades.

## Simple deployment inputs

A local install starts with `make quickstart`; `make configure` prepares it
without starting containers. The six choices in [`.env.example`](../../.env.example)
are the basic entry point. The grouped [complete reference](../../deploy/config/.env.reference)
contains optional overrides, not another runtime configuration file. Only `.env`
is loaded by default, with explicit environment overrides taking precedence.
An existing custom `OPENCITADEL_ENV_FILE` remains supported. Compose and
Helm derive their own internal service addresses, sandbox network/namespace,
OAuth defaults and cookie security from deployment inputs.

Credentials are generated once, independently, and saved in private `.env` or
role-specific Kubernetes Secrets. Restarts and Helm upgrades reuse them.
Existing valid variable names remain supported; existing signing-key fallbacks
remain compatible. Never regenerate keys when recovering an existing database.
The isolated `.env.e2e` remains an acceptance fixture, not a copy of every default.

Helm production needs a real HTTPS `publicUrl` (or an explicit enabled Ingress
host); local chart evaluation uses an explicit `env.ENV=development`. TLS,
external-service credentials and trusted proxy exceptions describe the actual
infrastructure and cannot be inferred from a sample domain.

## Runtime Policy boundaries

- Execution Policy is frozen into every admitted Run. In-flight Runs keep their
  original revision and policy snapshot.
- Operations Policy controls live admission, traffic, scheduling, sandbox
  creation, patrol posture, source access, garbage collection, and retention.
- Repository refresh verifies the head/revision pair, schema version, and digest;
  fresh consumers enforce verified-cache age. Integrity/persistent policy failure
  and stale state fail closed. Transient storage failure may use the last
  verified pair within the maximum staleness window, while readiness is unavailable.
- Updates use head-version compare-and-swap. A conflict preserves the admin's
  draft and requires an explicit reload or retry.
- Restore creates a new revision; history is never mutated.

## Deployment and Integration boundaries

Deployment Settings describe process topology and operational safety/maintenance
limits. Domain policy fields are not duplicated there. Sandbox topology is deployment-owned, while each authenticated
create request carries the active Operations Policy revision and resource limits.

Inference, MCP, and A2A are first-class owner-scoped resources. Credentials are
stored in versioned encrypted envelopes and masked on reads. Stable IDs bind
Skills, Automations, and execution requests; display names are not identities.

Signing and token secrets follow an active/previous ring so they can rotate
without downtime: `API_KEY_SECRET`/`API_KEY_PREVIOUS_SECRETS`,
`AUDIT_SIGNING_KEY`/`AUDIT_PREVIOUS_SIGNING_KEYS`, and
`JWT_SECRET`/`JWT_PREVIOUS_SECRETS`. `DATABASE_AUTHORIZATION_SIGNING_SECRET`
optionally splits the database authorization HMAC from `SESSION_SECRET` and
falls back to it when unset. `SANDBOX_TOKEN_SEED` derives per-sandbox data-plane
tokens, and `OPS_ACTUATOR_TOKEN`/`OPS_COLLECTOR_TOKEN` gate the Ops MCP servers.
Advanced execution-kernel tuning (`EXECUTION_ACTIVITY_MAX_CONCURRENCY`,
`EXECUTION_ACTIVITY_BATCH_SIZE`, `EXECUTION_IDLE_POLL_SECONDS`) is deployment
topology, not runtime behavior, so it stays in the environment rather than
Runtime Policy. The same Settings model currently owns
`EXECUTION_MAX_ACTIVE_RUNS_PER_SCOPE` (default 200), claim-attempt ceilings,
execution queue retention and purge batches, and `RECYCLE_BIN_RETENTION_DAYS`
(default 30). These are explicit operational limits; knowledge version GC
and Patrol retention remain Operations Policy.

## Change rules

- Add a policy field only in the typed model, initial seed, admin form, OpenAPI
  contract, tests, and Runtime Policy architecture/operations documentation.
- Add a deployment setting in `core/config.py`, `deploy/config/.env.reference`,
  the relevant Compose/Helm adapter, validation schemas and documentation. Add it
  to `.env.example` only if it belongs to basic startup choices.
- Never copy a value between these authorities or introduce a fallback path.
- Never place secrets in Runtime Policy revisions, Run public projections, or
  UI event payloads.

See [Runtime Policy Control Plane](runtime-policy-control-plane.md) for the
revision, consistency, and consumer model.
