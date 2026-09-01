# Configuration Sources and Governance

[简体中文](config-source-governance.zh-CN.md)

Every value has exactly one authority.

| Kind | Authority | Examples |
| --- | --- | --- |
| Deployment topology and secrets | Environment or secret manager | database identities, signing/encryption keys (`API_KEY_SECRET`, `AUDIT_SIGNING_KEY`, `JWT_SECRET`/`JWT_PREVIOUS_SECRETS`, `DATABASE_AUTHORIZATION_SIGNING_SECRET`, `SANDBOX_TOKEN_SEED`, `OPS_ACTUATOR_TOKEN`/`OPS_COLLECTOR_TOKEN`), OAuth, storage, sandbox driver/image/network |
| Live runtime behavior | PostgreSQL Runtime Policy head | admission, timeouts, retries, scheduler, sandbox limits, retention |
| Integrations | Owner-scoped PostgreSQL resources | inference endpoints/models/bindings, MCP, A2A, Skills |
| Product data | Domain tables | sessions, jobs, Packs, resources, versions |

There is no runtime YAML overlay, per-user behavior override, or environment
fallback for policy fields. Migration creates a typed Execution Policy revision,
a typed Operations Policy revision, and one atomic head that activates both.
Later changes are immutable revisions created through the admin API/UI.

## Runtime Policy boundaries

- Execution Policy is frozen into every admitted Run. In-flight Runs keep their
  original revision and policy snapshot.
- Operations Policy controls live admission, traffic, scheduling, sandbox
  creation, patrol posture, source access, garbage collection, and retention.
- Every read verifies the head/revision pair, schema version, digest, and
  staleness. Integrity, unavailable, or stale state fails closed.
- Updates use head-version compare-and-swap. A conflict preserves the admin's
  draft and requires an explicit reload or retry.
- Restore creates a new revision; history is never mutated.

## Deployment and Integration boundaries

Deployment Settings describe where and how processes run. They never carry
behavioral limits. Sandbox topology is deployment-owned, while each authenticated
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
Runtime Policy.

## Change rules

- Add a policy field only in the typed model, initial seed, admin form, OpenAPI
  contract, tests, and Runtime Policy architecture/operations documentation.
- Add a deployment setting only in `core/config.py`, `.env.example`, Compose,
  Helm, validation schemas, and deployment documentation.
- Never copy a value between these authorities or introduce a fallback path.
- Never place secrets in Runtime Policy revisions, Run public projections, or
  UI event payloads.

See [Runtime Policy Control Plane](runtime-policy-control-plane.md) for the
revision, consistency, and consumer model.
