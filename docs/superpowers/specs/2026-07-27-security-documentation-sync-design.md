# Security Documentation Synchronization Design

**Date:** 2026-07-27
**Status:** Approved
**Owner:** OpenCitadel maintainers

## Purpose

Bring every operator- and contributor-facing document affected by the
zero-trust hardening commit into agreement with the implementation, and add
repository checks that fail when critical security facts drift again.

This work changes documentation and documentation validation only. It does not
change runtime behavior, migrations, APIs, deployment manifests, or secrets.

## Source-of-truth hierarchy

Documentation statements must be derived from the following implementation
sources, in this order:

1. `api/core/config.py` for production startup requirements and secret rules.
2. `api/app/interfaces/` and `api/app/infrastructure/security/` for
   authentication, authorization, RLS context, rate limits, SSRF, and trusted
   client-IP behavior.
3. `api/alembic/versions/` and
   `deploy/helm/opencitadel/files/postgres/init-app-role.sh` for database
   ownership, RLS, audit-chain, and upgrade behavior.
4. `docker-compose.yml`, `deploy/squid/squid.conf`, and the Helm templates for
   deployment topology.
5. `.github/workflows/` and `.github/dependabot.yml` for CI, scanning, release,
   SBOM, and provenance claims.

If narrative documentation conflicts with these sources, the narrative is
wrong and must be corrected.

## Documentation changes

### 1. Security model

Update both:

- `docs/architecture/security-model.md`
- `docs/architecture/security-model.zh-CN.md`

The authorization section will describe the complete request path:

`Principal → WorkspaceContext/OwnerScope → immutable AuthorizationContext →
transaction-local PostgreSQL GUCs → FORCE ROW LEVEL SECURITY`.

It will also document:

- `USER`, `AUDITOR`, and `ADMIN`, including auditor read-only enforcement for
  Cookie-authenticated routes and service API keys.
- Personal, team, and global resource visibility.
- Global LLM endpoint/model, Skill, MCP, and A2A mutation rules.
- Dedicated personal/team model preferences instead of mutating global model
  rows.
- Exact rate-limit coverage: all `/api/` business endpoints are limited except
  `/api/status`, `/api/metrics`, and preflight requests; both client IP and
  every presented credential receive buckets.
- Secret lifecycle: `API_KEY_SECRET_ID`,
  `API_KEY_PREVIOUS_SECRETS`, automatic endpoint-key rotation,
  `AUDIT_SIGNING_KEY`, previous audit verification keys, and the distinction
  between tamper evidence and external immutable storage.
- Append-only audit rows, hash-chain verification, and alert behavior.

### 2. Production deployment guide

Update both:

- `docs/operations/deployment.md`
- `docs/operations/deployment.zh-CN.md`

Replace incomplete production environment examples with copyable templates
that contain every production bootstrap requirement:

- four distinct 32-or-more-character values for `API_KEY_SECRET`,
  `AUDIT_SIGNING_KEY`, `JWT_SECRET`, and `SESSION_SECRET`;
- a 32-or-more-character `SANDBOX_BROKER_TOKEN`;
- a strong bootstrap administrator password;
- distinct PostgreSQL admin and application credentials;
- a 16-or-more-character Redis password;
- secure Cookie, frontend, OAuth, trusted-proxy, outbound-port, and optional
  private-host settings.

The guide will explain that examples intentionally use placeholders and that
production startup rejects them.

Add executable operational procedures for:

- new Compose deployments;
- existing Compose volumes, including running
  `10-opencitadel-app-role.sh` before `opencitadel-migrate`;
- existing Helm PVCs, including a safe sequence for copying/executing the
  checked-in role migration script before the upgraded migration
  init-container starts;
- verification of `rolsuper=false`, `rolbypassrls=false`, relation ownership,
  migration head, Redis authentication, sandbox proxy health, and audit-chain
  integrity;
- API encryption-key rotation and audit-signing-key rotation;
- CI security gates and the distinction between local static validation and
  Docker/Helm/PostgreSQL-backed validation.

Commands must use placeholders or shell variables; no usable credentials may
appear in the repository.

### 3. API and Helm operator references

Update both language variants of:

- `api/README.md`
- `api/README.zh-CN.md`
- `deploy/helm/opencitadel/README.md`
- `deploy/helm/opencitadel/README.zh-CN.md`

API README changes:

- describe AuthorizationContext and RLS as defense in depth;
- explain model preference scope;
- state that auditor-owned service API keys cannot execute A2A operations;
- show current API-key and audit-key rotation variables and commands.

Helm README changes:

- provide an executable existing-PVC migration sequence;
- state all required secrets and distinctness requirements;
- list the sandbox NetworkPolicy requirement and production verification
  commands;
- explain that the application/migration role is non-superuser and subject to
  RLS.

### 4. CI and release documentation

The deployment guide and technical-decision/security sections will enumerate:

- API, UI, sandbox, Compose, Helm, and Squid validation;
- dependency review and Dependabot;
- Gitleaks history scanning;
- CodeQL;
- Trivy filesystem, IaC, and built-image scanning;
- full-SHA GitHub Action pinning;
- multi-architecture release images, SBOM, provenance, and attestations.

Claims will match the actual workflow triggers and severity thresholds.

### 5. Tutorials and examples

Review the self-host tutorial and `.env.example` for consistency with the
authoritative deployment guide. Only discrepancies introduced by the security
hardening are in scope. The quick-start tutorial may remain concise, but it
must link to the production guide and must not imply that placeholder secrets
are production-ready.

## Automated anti-drift checks

Extend `scripts/check-docs.sh` without adding a new runtime dependency.

The checker will fail when:

1. English/Chinese document pairs are missing.
2. Production deployment examples omit any required secret or database-role
   variable.
3. A production example contains `REDIS_PASSWORD=` with no value.
4. The security model omits AuthorizationContext, FORCE RLS, auditor
   read-only, scoped model preferences, audit signing keys, or the exact
   status/metrics rate-limit exemption.
5. Documentation reintroduces the obsolete instruction to re-save every model
   endpoint after key rotation.
6. The deployment guide fails to mention the security workflow, CodeQL,
   Gitleaks, Trivy, SBOM, and provenance.
7. Existing-volume/PVC instructions omit the checked-in role migration script
   or the non-bypass role verification.

Checks will match stable semantic markers rather than exact paragraphs so
normal copy editing does not cause false failures.

## Validation strategy

Run:

```bash
./scripts/check-docs.sh
git diff --check
bash -n scripts/check-docs.sh
sh -n deploy/helm/opencitadel/files/postgres/init-app-role.sh
```

Validate the checker itself with a red/green cycle:

1. Temporarily remove one required marker from a document.
2. Run `./scripts/check-docs.sh` and observe the intended failure.
3. Restore the marker through the planned documentation edit.
4. Run the checker again and require exit code 0.

Also parse all workflow YAML files and Helm values/schema with the repository's
installed Node `js-yaml`, then render Docker Compose with `.env.example`.

## Acceptance criteria

- Every implementation fact identified in the source-of-truth hierarchy has
  one authoritative operator-facing explanation.
- English and Chinese variants carry the same requirements and commands.
- Production examples satisfy the validation rules in `api/core/config.py`
  when placeholders are replaced with strong values.
- Existing Compose volume and Helm PVC upgrades have ordered, copyable
  procedures that preserve data and establish the non-bypass application role.
- Key rotation, audit verification, sandbox egress, and CI security gates are
  operationally testable from the documentation.
- `scripts/check-docs.sh`, shell syntax checks, workflow/schema parsing,
  Compose rendering, and `git diff --check` all pass.
- No runtime source or deployment behavior changes are included.

## Delivery

Work remains in the existing detached worktree. The design is committed first,
followed by a detailed implementation plan and the documentation/checker
changes. After final verification, the resulting descendant commit is
fast-forwarded to `main`; no branch or merge commit is created.
