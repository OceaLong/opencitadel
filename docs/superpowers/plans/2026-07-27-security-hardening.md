# OpenCitadel Security Hardening Implementation Plan

> Execute this plan in a detached worktree created from `main`. Use test-driven
> development for every behavior change and fast-forward the verified commits
> back to `main` without creating a long-lived branch.

**Goal:** Make authorization fail closed across tenant boundaries, prevent
untrusted users from controlling global model selection, and progressively
harden sandboxing, outbound access, deployment defaults, auditability, and CI.

**Security baseline:** Zero-trust multi-tenant deployment exposed to the
Internet and capable of processing attacker-controlled files, URLs, prompts,
tools, and code.

## Phase 0 — Immediate containment

### Task 1: Session resource authorization

- Add regression tests proving a session cannot reference a codebase,
  knowledge base, skill, or model outside its `OwnerScope`.
- Require scope-aware lookups during session creation.
- Re-check the same scope when constructing a task runner so persisted or
  legacy session identifiers cannot bypass authorization.
- Verify with targeted service and task-runner tests.

### Task 2: File and knowledge-base authorization

- Add regression tests proving marketplace operations pass `OwnerScope` to
  every file download and model resolution.
- Add regression tests proving knowledge-base ingestion validates file
  ownership before reading storage bytes.
- Remove optional-scope behavior from user-facing file paths; reserve any
  system bypass for an explicit capability type.
- Verify marketplace, file, knowledge-base, and route tests.

### Task 3: Global model control-plane protection

- Add regression tests proving private/team model creation or update cannot set
  `is_default`.
- Add regression tests proving first-user/private model creation cannot become
  the global default.
- Require scope for explicit model resolution; allow default resolution to
  select global models only.
- Restrict default clearing and fallback selection to global models.
- Propagate scope through sessions, marketplace, skills, probes, and other
  request-driven callers.
- Verify service, repository, endpoint, worker, and task-runner tests.

## Phase 1 — Central authorization boundary

- Introduce a mandatory `AuthorizationContext` containing principal, tenant,
  workspace/team, roles, and request identity.
- Replace optional repository scopes with explicit user or system
  capabilities.
- Add database tenant/team ownership columns where missing, including model
  records.
- Add PostgreSQL row-level-security policies as defense in depth.
- Add cross-tenant property tests covering every CRUD and execution endpoint.

## Phase 2 — Model and secret ownership

- Separate global model defaults from personal/team preferences.
- Store preference bindings in dedicated user/team tables instead of mutable
  model rows.
- Encrypt provider credentials with a dedicated versioned key and rotation
  workflow.
- Audit all create/update/delete/default/probe operations without recording
  secrets.

## Phase 3 — Untrusted execution sandbox

- Remove host Docker socket mounts from Internet-facing API and worker
  containers.
- Move sandbox lifecycle control behind a narrow authenticated broker.
- Enforce non-root users, dropped capabilities, read-only root filesystems,
  no-new-privileges, seccomp/AppArmor, PID/memory/CPU/time limits, and ephemeral
  workspaces.
- Disable unsafe Chrome flags and run browser workloads in a stronger isolation
  boundary.
- Add escape, resource-exhaustion, persistence, and cross-job isolation tests.

## Phase 4 — Outbound network policy

- Centralize URL validation for MCP, A2A, knowledge-base imports, webhooks, and
  browser navigation.
- Allow only approved schemes and ports; reject credentials, loopback, link
  local, private, multicast, reserved, and cloud-metadata targets.
- Pin validated DNS results at connection time or enforce the policy in an
  egress proxy to prevent DNS rebinding and TOCTOU bypasses.
- Add redirect-hop validation, response-size limits, and strict timeouts.
- Add IPv4, IPv6, encoded-address, redirect, and rebinding tests.

## Phase 5 — Edge, secrets, and deployment

- Define trusted proxy boundaries and canonical client-IP extraction.
- Apply rate limits per authenticated principal and trusted client IP.
- Align Helm secret keys with consuming workloads and make deployment smoke
  tests fail on missing keys.
- Either configure Redis authentication end to end or remove the unused client
  password setting.
- Replace permissive example secrets and fail startup on production defaults.

## Phase 6 — Tamper-evident audit and incident readiness

- Use a dedicated audit signing key instead of the API credential key.
- Record actor, tenant, action, target, decision, reason, request ID, and
  before/after metadata for high-risk operations.
- Store append-only audit records with chained hashes or an external immutable
  sink.
- Add export, retention, alerting, and signature-verification tests.

## Phase 7 — CI security gates

- Make backend/unit, frontend test, typecheck, lint, sandbox, deployment-render,
  and end-to-end authorization suites mandatory.
- Add dependency, secret, container, IaC, and SAST scanning with explicit
  severity budgets.
- Add adversarial evaluations for prompt/tool boundary violations and unsafe
  outbound actions.

## Verification gates

Run after each task:

```bash
/Users/longhaiyang/code/agent/opencitadel/api/.venv/bin/pytest <targeted-tests>
```

Run before integration:

```bash
cd api && /Users/longhaiyang/code/agent/opencitadel/api/.venv/bin/pytest
cd ../sandbox && /Users/longhaiyang/code/agent/opencitadel/sandbox/.venv/bin/pytest
cd ../ui && npm test
cd ../ui && npm run typecheck
cd ../ui && npm run lint
git diff --check
```

Known baseline failures must be recorded separately from regressions. No task is
complete until its new tests have been observed failing for the intended reason
and then passing after the minimal implementation.
