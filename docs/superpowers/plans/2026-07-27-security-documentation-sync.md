# Security Documentation Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize every security-relevant operator document with the
zero-trust implementation and make critical facts fail closed against future
documentation drift.

**Architecture:** Treat runtime configuration, authorization code, migrations,
deployment artifacts, and GitHub workflows as the source of truth. Extend the
existing Bash documentation checker with stable semantic requirements, then
update the English and Chinese documents until those checks pass. Keep all
changes documentation-only except for the documentation validation script.

**Tech Stack:** Markdown, Bash, ripgrep, Docker Compose v2, Node.js with the
repository-installed `js-yaml`, JSON Schema, Helm templates.

## Global Constraints

- Work only in
  `/Users/longhaiyang/code/agent/opencitadel/.worktrees/security-hardening`.
- Remain on detached `HEAD`; create no branch and no merge commit.
- Do not modify runtime Python, migrations, deployment manifests, workflows,
  application configuration, or secret values.
- Keep English and Simplified Chinese documents equivalent in requirements and
  executable commands.
- Use placeholders or shell variables in commands; never add usable
  credentials.
- Preserve the source-of-truth order defined in
  `docs/superpowers/specs/2026-07-27-security-documentation-sync-design.md`.
- The four production secrets `API_KEY_SECRET`, `AUDIT_SIGNING_KEY`,
  `JWT_SECRET`, and `SESSION_SECRET` are distinct and at least 32 characters
  after placeholder substitution.
- `SANDBOX_BROKER_TOKEN` is at least 32 characters when the broker URL is
  configured; `BOOTSTRAP_ADMIN_PASSWORD` is at least 12 characters;
  `POSTGRES_PASSWORD` and `REDIS_PASSWORD` are at least 16 characters.
- The PostgreSQL admin role/password and application role/password are
  distinct; the application/migration role is `NOSUPERUSER NOBYPASSRLS`.
- The documentation checker remains dependency-free beyond tools already used
  by the repository.
- Use `bash -n`, not `sh -n`, for `scripts/check-docs.sh` because it uses Bash
  process substitution.

---

### Task 1: Lock the approved specification and executable baseline

**Files:**

- Modify:
  `docs/superpowers/specs/2026-07-27-security-documentation-sync-design.md:3-4`
- Modify:
  `docs/superpowers/specs/2026-07-27-security-documentation-sync-design.md:172-181`
- Create:
  `docs/superpowers/plans/2026-07-27-security-documentation-sync.md`

**Interfaces:**

- Consumes: approved design and the existing detached worktree.
- Produces: an approved specification with an executable Bash syntax command
  and this task-by-task plan.

- [ ] **Step 1: Record approval and correct the shell validation command**

  Set the design status to `Approved` and replace
  `sh -n scripts/check-docs.sh` with `bash -n scripts/check-docs.sh`.

- [ ] **Step 2: Verify the documentation baseline**

  Run:

  ```bash
  ./scripts/check-docs.sh
  bash -n scripts/check-docs.sh
  sh -n deploy/helm/opencitadel/files/postgres/init-app-role.sh
  git diff --check
  ```

  Expected: all four commands exit `0`. The earlier `sh -n
  scripts/check-docs.sh` failure is not a repository defect; it invokes the
  Bash script with the wrong shell.

- [ ] **Step 3: Self-review the plan**

  Run:

  ```bash
  rg -n 'T[B]D|T[O]DO|implement l[a]ter|fill in d[e]tails|[Ss]imilar t[o]' \
    docs/superpowers/plans/2026-07-27-security-documentation-sync.md
  git diff --check
  ```

  Expected: the placeholder scan returns no matches and `git diff --check`
  exits `0`.

- [ ] **Step 4: Commit the approved specification and plan**

  ```bash
  git add \
    docs/superpowers/specs/2026-07-27-security-documentation-sync-design.md \
    docs/superpowers/plans/2026-07-27-security-documentation-sync.md
  git commit -m "docs: plan security documentation synchronization"
  ```

### Task 2: Add fail-closed documentation security contracts

**Files:**

- Modify: `scripts/check-docs.sh:1-125`
- Test through: `./scripts/check-docs.sh`

**Interfaces:**

- Consumes: repository-relative Markdown paths and the existing `fail`
  accumulator.
- Produces: `require_marker <file> <literal> <description>` and security
  drift checks whose process exit code is non-zero whenever a contract is
  absent.

- [ ] **Step 1: Add the failing contract checks**

  Add this helper after `check_pair_dir`:

  ```bash
  require_marker() {
    local file="$1"
    local marker="$2"
    local description="$3"
    if ! grep -Fq -- "$marker" "$file"; then
      fail "$file missing $description marker: $marker"
    fi
  }
  ```

  Add checks for both English and Chinese security-model files requiring these
  literal implementation identifiers:

  ```text
  AuthorizationContext
  FORCE ROW LEVEL SECURITY
  AUDITOR
  llm_model_preferences
  AUDIT_SIGNING_KEY
  /api/status
  /api/metrics
  OPTIONS
  ```

  For both production deployment guides, require:

  ```text
  API_KEY_SECRET
  API_KEY_SECRET_ID
  API_KEY_PREVIOUS_SECRETS
  AUDIT_SIGNING_KEY
  AUDIT_SIGNING_KEY_ID
  AUDIT_PREVIOUS_SIGNING_KEYS
  JWT_SECRET
  SESSION_SECRET
  SANDBOX_BROKER_TOKEN
  BOOTSTRAP_ADMIN_PASSWORD
  POSTGRES_ADMIN_USER
  POSTGRES_ADMIN_PASSWORD
  POSTGRES_USER
  POSTGRES_PASSWORD
  REDIS_PASSWORD
  COOKIE_SECURE
  FRONTEND_BASE_URL
  OAUTH_REDIRECT_BASE
  TRUSTED_PROXY_CIDRS
  OUTBOUND_ALLOWED_PORTS
  OUTBOUND_PRIVATE_HOST_ALLOWLIST
  ```

  Also require these operational markers in both deployment guides:

  ```text
  10-opencitadel-app-role.sh
  rolsuper
  rolbypassrls
  security.yml
  Gitleaks
  CodeQL
  Trivy
  SBOM
  provenance
  ```

  Require `files/postgres/init-app-role.sh`, `rolsuper`, and `rolbypassrls` in
  both Helm README variants.

  Add a repository search that rejects empty production examples:

  ```bash
  if rg -n '^REDIS_PASSWORD=[[:space:]]*$' \
    docs/operations/deployment.md \
    docs/operations/deployment.zh-CN.md 2>/dev/null; then
    fail "production deployment examples must not contain an empty REDIS_PASSWORD"
  fi
  ```

  Add a search that rejects the four known stale key-rotation instructions:

  ```bash
  if rg -n \
    'After rotating `API_KEY_SECRET`, re-save|Key rotation requires re-saving|轮换 `API_KEY_SECRET` 后.*重新保存|轮换 secret 需在 UI 重新保存' \
    docs api/README.md api/README.zh-CN.md 2>/dev/null; then
    fail "found obsolete manual endpoint re-save instruction for API key rotation"
  fi
  ```

- [ ] **Step 2: Run the checker and verify RED**

  Run:

  ```bash
  ./scripts/check-docs.sh
  ```

  Expected: non-zero exit with missing-marker errors for the security model,
  deployment guide, and Helm README, plus stale rotation/empty Redis findings.
  This proves the new contract detects the current drift.

- [ ] **Step 3: Validate checker syntax**

  Run:

  ```bash
  bash -n scripts/check-docs.sh
  ```

  Expected: exit `0`; the failing behavior must come from contract violations,
  not shell syntax.

### Task 3: Synchronize the bilingual security architecture

**Files:**

- Modify: `docs/architecture/security-model.md:167-288`
- Modify: `docs/architecture/security-model.zh-CN.md:167-288`
- Modify: `docs/architecture/technical-decisions.md:309-351`
- Modify: `docs/architecture/technical-decisions.zh-CN.md:309-351`

**Interfaces:**

- Consumes: `Principal`, `WorkspaceContext`, `OwnerScope`,
  `AuthorizationContext`, PostgreSQL transaction GUCs, FORCE RLS policies,
  scoped model preferences, rate-limit middleware, versioned key rings, and
  audit-chain verification.
- Produces: one bilingual architectural explanation matching those runtime
  contracts.

- [ ] **Step 1: Replace the authorization flow and role table**

  Document this full path in prose and Mermaid:

  ```text
  Principal → WorkspaceContext / OwnerScope → immutable AuthorizationContext
  → transaction-local PostgreSQL GUCs → FORCE ROW LEVEL SECURITY
  ```

  State that:

  - `USER` can use personal and member team scopes.
  - `AUDITOR` can read compliance/admin evidence but all authenticated
    mutations are denied; auditor-owned service API keys are also denied.
  - `ADMIN` can use platform administration and global configuration routes.
  - Cross-scope resource lookups use not-found responses when existence would
    leak.

- [ ] **Step 2: Document visibility and model preference ownership**

  Add a table covering personal, team, and global resources. State that global
  LLM endpoints/models, Skills, MCP servers, and A2A servers are mutated only
  by platform admins. Explain that personal/team defaults are rows in
  `llm_model_preferences`, rather than mutations of global `llm_models`.

- [ ] **Step 3: Correct rate-limit semantics**

  Replace the stale public-endpoint statement with the exact behavior:

  - every business path under `/api/` is limited;
  - `/api/status`, `/api/metrics`, and `OPTIONS` are exempt;
  - IP and every presented access cookie, refresh cookie, and `X-Api-Key`
    credential each receive a bucket;
  - raw tokens are never stored in rate-limit keys;
  - production rejects requests with `503` if Redis-backed limiting is
    unavailable.

- [ ] **Step 4: Replace key-management and audit-chain guidance**

  Document the ordered API-key rotation:

  1. retain the old key in `API_KEY_PREVIOUS_SECRETS`;
  2. set a new `API_KEY_SECRET` and `API_KEY_SECRET_ID`;
  3. run `python -m app.migrate_llm_api_key_rotation`;
  4. verify all rows are `fernet_v2` under the new key id;
  5. remove the old key only after rollback and verification windows close.

  Document equivalent audit verification-ring handling with
  `AUDIT_SIGNING_KEY`, `AUDIT_SIGNING_KEY_ID`, and
  `AUDIT_PREVIOUS_SIGNING_KEYS`. Explain that database triggers prevent audit
  `UPDATE`/`DELETE`, chain verification emits
  `AUDIT_CHAIN_INTEGRITY_FAILURE`, and external immutable storage is still
  needed against privileged database destruction.

- [ ] **Step 5: Update the Fernet technical decision**

  Replace the obsolete single-key/manual-resave disadvantage with versioned
  `fernet_v2` ciphertext, explicit key ids, previous-key compatibility, and
  automated idempotent rotation. Retain the real limitation that self-hosted
  symmetric keys are exportable and are not an HSM/KMS substitute.

- [ ] **Step 6: Run focused drift checks**

  Run:

  ```bash
  rg -n \
    'AuthorizationContext|FORCE ROW LEVEL SECURITY|AUDITOR|llm_model_preferences|AUDIT_SIGNING_KEY|/api/status|/api/metrics|OPTIONS' \
    docs/architecture/security-model.md \
    docs/architecture/security-model.zh-CN.md
  rg -n \
    'After rotating `API_KEY_SECRET`, re-save|Key rotation requires re-saving|轮换 `API_KEY_SECRET` 后.*重新保存|轮换 secret 需在 UI 重新保存' \
    docs/architecture api/README.md api/README.zh-CN.md
  git diff --check
  ```

  Expected: all required markers appear in both security-model files, the
  stale-instruction search returns no matches, and whitespace validation exits
  `0`.

### Task 4: Make production deployment and rotation procedures executable

**Files:**

- Modify: `docs/operations/deployment.md:122-241`
- Modify: `docs/operations/deployment.md:317-334`
- Modify: `docs/operations/deployment.md:508-620`
- Modify: `docs/operations/deployment.md:780-812`
- Modify: `docs/operations/deployment.zh-CN.md:122-241`
- Modify: `docs/operations/deployment.zh-CN.md:317-334`
- Modify: `docs/operations/deployment.zh-CN.md:508-620`
- Modify: `docs/operations/deployment.zh-CN.md:776-808`

**Interfaces:**

- Consumes: production validation in `api/core/config.py`, Compose bootstrap
  role script, Helm PostgreSQL ConfigMap/StatefulSet, audit verification API,
  Squid egress topology, and CI/security/release workflows.
- Produces: copyable bilingual new-deployment, existing-volume, key-rotation,
  verification, and release-gate procedures.

- [ ] **Step 1: Replace both production environment templates**

  Include every variable required by Task 2. Use generated shell variables,
  for example:

  ```bash
  API_KEY_SECRET="$(openssl rand -hex 32)"
  AUDIT_SIGNING_KEY="$(openssl rand -hex 32)"
  JWT_SECRET="$(openssl rand -hex 32)"
  SESSION_SECRET="$(openssl rand -hex 32)"
  SANDBOX_BROKER_TOKEN="$(openssl rand -hex 32)"
  ```

  Explain that these five values are generated separately, placeholders are
  rejected by `ENV=production`, PostgreSQL credentials are distinct, and
  Redis authentication is mandatory.

- [ ] **Step 2: Separate new and existing Compose database procedures**

  For a new volume, explain that
  `/docker-entrypoint-initdb.d/10-opencitadel-app-role.sh` runs automatically.
  For an existing volume, order the commands as:

  1. backup;
  2. set both admin and app credentials;
  3. start only PostgreSQL;
  4. execute the checked-in idempotent role/ownership script;
  5. verify `rolsuper=false`, `rolbypassrls=false`, and `wrong_owner=0`;
  6. run `opencitadel-migrate`;
  7. start API/Worker and validate status.

- [ ] **Step 3: Replace manual endpoint resave with key-ring rotation**

  Provide Compose commands for
  `python -m app.migrate_llm_api_key_rotation`, database queries for
  `fernet_v2`, and rollback-window guidance. Add audit signing-key rotation
  with the old key in `AUDIT_PREVIOUS_SIGNING_KEYS` and verify via
  `GET /api/admin/audit/verify-chain` before removing it.

- [ ] **Step 4: Add production verification matrix**

  Add commands or observable results for:

  - application role flags and relation ownership;
  - Alembic head;
  - authenticated Redis `PING`;
  - sandbox broker and Squid proxy health;
  - global audit-chain verification.

- [ ] **Step 5: Document CI and release gates exactly**

  Enumerate:

  - `ci.yml`: API, UI, sandbox, five image builds/scans, Compose, Helm, Squid,
    and docs checks;
  - `security.yml`: Gitleaks history, dependency review at `high`, Python/npm
    audits, CodeQL security-extended for Python and TypeScript, and Trivy
    filesystem/IaC at `HIGH,CRITICAL`;
  - `dependabot.yml`: Actions, uv, npm, and Docker updates;
  - `release.yml`: full-SHA action pinning, five `linux/amd64` +
    `linux/arm64` images, Trivy digest scans, SBOM, maximum provenance, and
    registry attestations.

- [ ] **Step 6: Run focused deployment checks**

  Run:

  ```bash
  rg -n '^REDIS_PASSWORD=[[:space:]]*$' \
    docs/operations/deployment.md \
    docs/operations/deployment.zh-CN.md
  rg -n \
    '10-opencitadel-app-role.sh|rolsuper|rolbypassrls|security.yml|Gitleaks|CodeQL|Trivy|SBOM|provenance' \
    docs/operations/deployment.md \
    docs/operations/deployment.zh-CN.md
  git diff --check
  ```

  Expected: empty-Redis search returns no matches; all operational markers
  appear in both files; whitespace validation exits `0`.

### Task 5: Synchronize API, Helm, and quick-start references

**Files:**

- Modify: `api/README.md:108-220`
- Modify: `api/README.md:263-345`
- Modify: `api/README.zh-CN.md:108-220`
- Modify: `api/README.zh-CN.md:263-345`
- Modify: `deploy/helm/opencitadel/README.md:14-105`
- Modify: `deploy/helm/opencitadel/README.zh-CN.md:14-105`
- Modify: `docs/tutorials/01-self-host-10-minutes.md:15-35`
- Modify: `docs/tutorials/01-self-host-10-minutes.zh-CN.md:15-35`
- Review without changing unless inconsistent: `.env.example:1-80`

**Interfaces:**

- Consumes: the authoritative security and deployment guides from Tasks 3-4.
- Produces: concise developer/operator entry points that do not contradict the
  authoritative guides.

- [ ] **Step 1: Add the API authorization contract**

  In both API READMEs, explain request-scoped `AuthorizationContext`,
  transaction-local GUCs, FORCE RLS defense in depth, personal/team model
  preferences, global-admin mutations, and read-only `AUDITOR` behavior.
  Explicitly state that an auditor-owned service API key cannot invoke A2A.

- [ ] **Step 2: Add API key-ring operations**

  Extend the encryption section with `fernet_v2`,
  `API_KEY_SECRET_ID`, `API_KEY_PREVIOUS_SECRETS`, the idempotent rotation
  command, and the audit signing-key ring. Link to the production deployment
  guide for ordered rollback-safe procedures.

- [ ] **Step 3: Add an executable existing-PVC Helm sequence**

  In both Helm READMEs provide commands that:

  1. set `NS`, `RELEASE`, and `PG_POD`;
  2. render/apply only the upgraded Secret and PostgreSQL init ConfigMap while
     the old workload still runs;
  3. copy the repository script with `kubectl cp`;
  4. execute it inside the current PostgreSQL Pod with the Pod's existing
     environment;
  5. verify `rolsuper`, `rolbypassrls`, and relation ownership;
  6. perform `helm upgrade` only after those checks.

  State that the procedure applies only to chart-managed PostgreSQL; external
  PostgreSQL operators must run the same script under their own admin channel.

- [ ] **Step 4: Add Helm security prerequisites and verification**

  List all required secrets, four-secret distinctness, PostgreSQL password
  distinctness, Redis authentication, `networkPolicy.enabled=true`, trusted
  ingress CIDRs, and exact-host private egress allowlists. Include verification
  for the non-bypass app role, NetworkPolicy rendering, rollout status, and
  `/api/status`.

- [ ] **Step 5: Correct quick-start production expectations**

  State in both tutorials that `make quickstart` is a local evaluation path,
  intentionally sets `COOKIE_SECURE=false`, and must not be promoted to
  production. Link to the production deployment guide before any public or
  multi-user deployment. Correct the Chinese Models link to
  `../operations/deployment.zh-CN.md#模型skill-与记忆`.

  Review `.env.example`; retain it unchanged if it already contains every
  production variable and explicit placeholder warnings.

- [ ] **Step 6: Run focused reference checks**

  Run:

  ```bash
  rg -n \
    'AuthorizationContext|FORCE RLS|AUDITOR|llm_model_preferences|API_KEY_PREVIOUS_SECRETS|AUDIT_PREVIOUS_SIGNING_KEYS' \
    api/README.md api/README.zh-CN.md
  rg -n \
    'files/postgres/init-app-role.sh|kubectl cp|rolsuper|rolbypassrls|networkPolicy.enabled' \
    deploy/helm/opencitadel/README.md \
    deploy/helm/opencitadel/README.zh-CN.md
  git diff --check
  ```

  Expected: each marker appears in both language variants and whitespace
  validation exits `0`.

### Task 6: Complete RED/GREEN verification, audit, and integration

**Files:**

- Modify if review finds omissions: all files changed in Tasks 1-5.
- Test: `scripts/check-docs.sh`

**Interfaces:**

- Consumes: every deliverable and acceptance criterion in the approved design.
- Produces: verified documentation commits and a fast-forwarded `main`.

- [ ] **Step 1: Verify the checker is GREEN**

  Run:

  ```bash
  ./scripts/check-docs.sh
  bash -n scripts/check-docs.sh
  sh -n deploy/helm/opencitadel/files/postgres/init-app-role.sh
  ```

  Expected: all commands exit `0`.

- [ ] **Step 2: Pressure-test one checker contract**

  Copy the working tree to a temporary directory that excludes `.git`, remove
  the `AuthorizationContext` marker from the copied English security model,
  and execute the copied checker.

  Expected: the copied checker exits non-zero with the missing
  `AuthorizationContext` message. Do not mutate the real working tree.

- [ ] **Step 3: Parse workflows and Helm values/schema**

  Run Node.js with `./ui/node_modules/js-yaml` to parse:

  ```text
  .github/workflows/ci.yml
  .github/workflows/security.yml
  .github/workflows/release.yml
  .github/dependabot.yml
  deploy/helm/opencitadel/values.yaml
  ```

  Parse `deploy/helm/opencitadel/values.schema.json` with `JSON.parse`.
  Expected: all files parse without exceptions.

- [ ] **Step 4: Render deployment inputs**

  Run:

  ```bash
  docker compose --env-file .env.example config --quiet
  ```

  If Helm is installed, additionally run `helm lint` and `helm template` with
  non-placeholder test secrets satisfying the schema. If Helm is unavailable,
  record the tool absence and rely on schema/YAML parsing plus the existing CI
  `deployment-render` gate; do not install new tools.

- [ ] **Step 5: Audit scope and bilingual parity**

  Run:

  ```bash
  git diff --check
  git status --short
  git diff --stat 29de99a
  git diff --name-only 29de99a
  ```

  Confirm that only the approved specification, plan, documentation files, and
  `scripts/check-docs.sh` changed. Compare each English/Chinese pair section by
  section and correct any missing requirement or command.

- [ ] **Step 6: Commit documentation and checker changes**

  ```bash
  git add \
    scripts/check-docs.sh \
    docs/architecture/security-model.md \
    docs/architecture/security-model.zh-CN.md \
    docs/architecture/technical-decisions.md \
    docs/architecture/technical-decisions.zh-CN.md \
    docs/operations/deployment.md \
    docs/operations/deployment.zh-CN.md \
    api/README.md \
    api/README.zh-CN.md \
    deploy/helm/opencitadel/README.md \
    deploy/helm/opencitadel/README.zh-CN.md \
    docs/tutorials/01-self-host-10-minutes.md \
    docs/tutorials/01-self-host-10-minutes.zh-CN.md
  git commit -m "docs: align security operations with zero trust"
  ```

- [ ] **Step 7: Run fresh post-commit verification**

  Re-run Steps 1, 3, 4, and 5 against committed `HEAD`. Expected: all available
  commands exit `0`, the worktree is clean, and the commit remains a descendant
  of `main`.

- [ ] **Step 8: Fast-forward the main branch without creating a branch**

  In the primary checkout, verify it is clean and still at the ancestor used
  by this worktree, then run:

  ```bash
  git merge --ff-only <verified-detached-head>
  ```

  Expected: `main` advances without a merge commit. Verify both checkouts point
  to the intended descendant and report the exact commit ids.
