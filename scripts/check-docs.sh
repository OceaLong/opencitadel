#!/usr/bin/env bash
# Lightweight documentation consistency checks.
set -euo pipefail

command -v rg >/dev/null 2>&1 || { echo "ERROR: ripgrep (rg) is required for content checks" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

errors=0

fail() {
  echo "ERROR: $*" >&2
  errors=$((errors + 1))
}

check_pair() {
  local en="$1"
  local zh="${en%.md}.zh-CN.md"
  if [[ ! -f "$en" ]]; then
    fail "missing English doc: $en"
    return
  fi
  if [[ ! -f "$zh" ]]; then
    fail "missing Chinese pair for: $en (expected $zh)"
  fi
}

check_pair_dir() {
  local dir="$1"
  if [[ ! -d "$dir" ]]; then
    return
  fi
  echo "==> Checking bilingual pairs in $dir ..."
  while IFS= read -r -d '' en; do
    base="$(basename "$en")"
    case "$base" in
      README.md|MAINTENANCE_CHECKLIST.md|DOCUMENTATION_INVENTORY.md) continue ;;
    esac
    check_pair "$en"
  done < <(find "$dir" -name '*.md' ! -name '*.zh-CN.md' -print0 2>/dev/null)

  while IFS= read -r -d '' zh; do
    en="${zh%.zh-CN.md}.md"
    if [[ ! -f "$en" ]]; then
      fail "orphan Chinese doc without English pair: $zh (expected $en)"
    fi
  done < <(find "$dir" -name '*.zh-CN.md' -print0 2>/dev/null)
}

require_marker() {
  local file="$1"
  local marker="$2"
  local description="$3"
  if ! grep -Fq -- "$marker" "$file"; then
    fail "$file missing $description marker: $marker"
  fi
}

echo "==> Checking docs/ bilingual pairs ..."
check_pair_dir docs/architecture
check_pair_dir docs/operations
check_pair_dir docs/tutorials
check_pair docs/DOCUMENTATION_INVENTORY.md

check_size_parity() {
  local dir="$1" threshold="$2"
  echo "==> Checking bilingual size parity in $dir (zh >= ${threshold}% of en lines) ..."
  while IFS= read -r -d '' en; do
    local zh="${en%.md}.zh-CN.md"
    [[ -f "$zh" ]] || continue
    local en_lines zh_lines
    en_lines=$(wc -l < "$en")
    zh_lines=$(wc -l < "$zh")
    if (( zh_lines * 100 < en_lines * threshold )); then
      fail "$zh is only $((zh_lines * 100 / en_lines))% of $en by lines (min ${threshold}%) — content drift"
    fi
  done < <(find "$dir" -name '*.md' ! -name '*.zh-CN.md' -print0 2>/dev/null)
}
check_size_parity docs/tutorials 60

echo "==> Checking module README bilingual pairs ..."
for en in README.md api/README.md ui/README.md sandbox/README.md nginx/README.md \
  ops-collector/README.md ops-actuator/README.md \
  deploy/helm/opencitadel/README.md demo/ops-console/README.md \
  deploy/patrol-demo/README.md e2e/README.md scripts/README.md deploy/scripts/README.md; do
  check_pair "$en"
done

echo "==> Checking .github/ bilingual pairs ..."
for en in .github/CONTRIBUTING.md .github/SECURITY.md .github/CODE_OF_CONDUCT.md \
  .github/pull_request_template.md; do
  check_pair "$en"
done

echo "==> Checking tutorial 05 in indexes ..."
for f in docs/README.md docs/README.zh-CN.md README.md README.zh-CN.md; do
  if ! grep -q '05-refund-reconciliation-compliance' "$f"; then
    fail "tutorial 05 not listed in $f"
  fi
done

echo "==> Checking Ops Patrol documentation chain ..."
for f in docs/README.md docs/README.zh-CN.md README.md README.zh-CN.md; do
  if ! grep -q 'ops-patrol' "$f"; then
    fail "Ops Patrol not listed in $f"
  fi
done
check_pair docs/architecture/ops-patrol.md
check_pair docs/operations/ops-patrol.md
check_pair docs/tutorials/06-ops-patrol.md
check_pair ops-collector/README.md
for f in docs/DOCUMENTATION_INVENTORY.md docs/DOCUMENTATION_INVENTORY.zh-CN.md; do
  for marker in architecture/ops-patrol operations/ops-patrol tutorials/06-ops-patrol ops-collector/README; do
    require_marker "$f" "$marker" "Ops Patrol inventory"
  done
done

echo "==> Checking new architecture docs in indexes ..."
for topic in inference-control-plane frontend-ui execution-kernel technical-decisions knowledge-base-ingestion; do
  for f in docs/README.md docs/README.zh-CN.md; do
    if ! grep -q "$topic" "$f"; then
      fail "architecture doc $topic not listed in $f"
    fi
  done
done

echo "==> Checking knowledge-base-ingestion bilingual pair ..."
check_pair docs/architecture/knowledge-base-ingestion.md

echo "==> Checking nginx README bilingual pair ..."
check_pair nginx/README.md

echo "==> Checking inventory technical-decisions diagram field ..."
if rg -n 'technical-decisions\.md.*\| none \|' docs/DOCUMENTATION_INVENTORY.md \
  docs/DOCUMENTATION_INVENTORY.zh-CN.md 2>/dev/null; then
  fail "technical-decisions should be marked mermaid in DOCUMENTATION_INVENTORY"
fi

echo "==> Checking stale single 200MB upload claim for KB ..."
if rg -n 'upload.*200\s*MB|200\s*MB.*upload' docs/tutorials/02-internal-knowledge-base.md \
  docs/tutorials/02-internal-knowledge-base.zh-CN.md 2>/dev/null | rg -v '50 MB|50\s*MB|gateway|网关|nginx'; then
  fail "KB tutorial should not imply 200MB document limit without the 50MB Execution Policy caveat"
fi

echo "==> Checking stale /settings/integrations references in docs ..."
if rg -n '/settings/integrations' docs README.md README.zh-CN.md ui/README.md ui/README.zh-CN.md \
  --glob '!MAINTENANCE_CHECKLIST*.md' 2>/dev/null; then
  fail "found stale /settings/integrations route in documentation (use Settings modal → Integrations tab)"
fi

echo "==> Checking stale /admin/usage UI route in docs ..."
if rg -n '\| `/admin/usage` \|' docs/architecture/admin-auditor-compliance.md \
  docs/architecture/admin-auditor-compliance.zh-CN.md 2>/dev/null; then
  fail "UI usage charts are on /admin overview, not /admin/usage"
fi

echo "==> Checking stale application-image count wording ..."
if rg -n 'four images|five images|six images|all five images|all six images|四镜像|五镜像|六镜像|五个镜像|六个镜像|扫描五个|扫描六个' \
  docs deploy/helm/opencitadel/README.md deploy/helm/opencitadel/README.zh-CN.md 2>/dev/null; then
  fail "documentation has a stale image count; releases publish seven images including ops-collector and ops-actuator"
fi
for f in docs/operations/deployment.md docs/operations/deployment.zh-CN.md; do
  require_marker "$f" "ops-actuator" "seven-image release count"
done

echo "==> Checking security architecture contracts ..."
for f in docs/architecture/security-model.md docs/architecture/security-model.zh-CN.md; do
  for marker in \
    AuthorizationContext \
    "FORCE ROW LEVEL SECURITY" \
    AUDITOR \
    inference_bindings \
    AUDIT_SIGNING_KEY \
    /api/status \
    /api/metrics \
    OPTIONS; do
    require_marker "$f" "$marker" "security architecture"
  done
done

echo "==> Checking production deployment contracts ..."
for f in docs/operations/deployment.md docs/operations/deployment.zh-CN.md; do
  for marker in \
    API_KEY_SECRET \
    API_KEY_SECRET_ID \
    API_KEY_PREVIOUS_SECRETS \
    AUDIT_SIGNING_KEY \
    AUDIT_SIGNING_KEY_ID \
    AUDIT_PREVIOUS_SIGNING_KEYS \
    JWT_SECRET \
    SESSION_SECRET \
    SANDBOX_BROKER_TOKEN \
    BOOTSTRAP_ADMIN_PASSWORD \
    POSTGRES_ADMIN_USER \
    POSTGRES_ADMIN_PASSWORD \
    POSTGRES_USER \
    POSTGRES_PASSWORD \
    REDIS_PASSWORD \
    COOKIE_SECURE \
    FRONTEND_BASE_URL \
    OAUTH_REDIRECT_BASE \
    TRUSTED_PROXY_CIDRS \
    OUTBOUND_ALLOWED_PORTS \
    OUTBOUND_PRIVATE_HOST_ALLOWLIST; do
    require_marker "$f" "$marker" "production configuration"
  done
  for marker in \
    files/postgres/init-app-role.sh \
    rolsuper \
    rolbypassrls \
    security.yml \
    Gitleaks \
    CodeQL \
    Trivy \
    SBOM \
    provenance; do
    require_marker "$f" "$marker" "production operation"
  done
done

echo "==> Checking Helm greenfield database-role contracts ..."
for f in deploy/helm/opencitadel/README.md deploy/helm/opencitadel/README.zh-CN.md; do
  require_marker "$f" "files/postgres/init-app-role.sh" "greenfield role bootstrap"
  require_marker "$f" "rolsuper" "runtime-role verification"
  require_marker "$f" "rolbypassrls" "runtime-role verification"
done

echo "==> Checking .env.example / .env.e2e key-set parity ..."
# .env.example is the single env schema. Every active key in it must also be
# declared in .env.e2e (values may differ); every key in .env.e2e must exist
# in .env.example at least as a commented-out entry. ACCEPTANCE_* keys are
# acceptance-run-only and exempt.
env_keys() { rg -o '^[A-Z][A-Z0-9_]*(?==)' -N --pcre2 "$1" | sort -u; }
env_keys_incl_commented() { rg -o '^#?\s*[A-Z][A-Z0-9_]*(?==)' -N --pcre2 "$1" | tr -d '# ' | sort -u; }
while IFS= read -r key; do
  fail ".env.example key $key is missing from .env.e2e (declare it, even empty)"
done < <(comm -23 <(env_keys .env.example) <(env_keys .env.e2e))
while IFS= read -r key; do
  case "$key" in ACCEPTANCE_*) continue ;; esac
  fail ".env.e2e key $key is not declared in .env.example (the env schema)"
done < <(comm -23 <(env_keys .env.e2e) <(env_keys_incl_commented .env.example))

echo "==> Checking production examples for empty Redis passwords ..."
if rg -n '^REDIS_PASSWORD=[[:space:]]*$' \
  docs/operations/deployment.md \
  docs/operations/deployment.zh-CN.md 2>/dev/null; then
  fail "production deployment examples must not contain an empty REDIS_PASSWORD"
fi

echo "==> Checking obsolete API key rotation instructions ..."
if rg -n \
  'After (rotation|rotating `API_KEY_SECRET`).*re-save|Key rotation requires re-saving|轮换(`API_KEY_SECRET` )?后.*重新保存|轮换 secret 需在 UI 重新保存' \
  docs/architecture/security-model.md \
  docs/architecture/security-model.zh-CN.md \
  docs/architecture/technical-decisions.md \
  docs/architecture/technical-decisions.zh-CN.md \
  docs/operations/deployment.md \
  docs/operations/deployment.zh-CN.md \
  api/README.md \
  api/README.zh-CN.md 2>/dev/null; then
  fail "found obsolete manual endpoint re-save instruction for API key rotation"
fi

echo "==> Checking explicit composition and lifecycle documentation ..."
for f in docs/architecture/overview.md docs/architecture/overview.zh-CN.md; do
  for marker in ApiRuntime KernelRuntime TaskSupervisor 'uow.commit()' post-commit 'userId + workspaceId'; do
    require_marker "$f" "$marker" "explicit composition"
  done
done
for f in docs/operations/deployment.md docs/operations/deployment.zh-CN.md; do
  for marker in /api/health/live /api/health/ready OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS; do
    require_marker "$f" "$marker" "runtime lifecycle"
  done
done
for f in docs/architecture/frontend-ui.md docs/architecture/frontend-ui.zh-CN.md; do
  require_marker "$f" 'userId + workspaceId' "authenticated cache scope"
done
if rg -n 'dependency-injector|dependency_injector|api/app/container\.py|app\.container' \
  README.md README.zh-CN.md api/README.md api/README.zh-CN.md \
  docs/architecture docs/operations docs/DOCUMENTATION_INVENTORY.md \
  docs/DOCUMENTATION_INVENTORY.zh-CN.md 2>/dev/null; then
  fail "found removed dependency-injector/container architecture in current documentation"
fi

echo "==> Checking deterministic acceptance documentation ..."
for f in e2e/README.md e2e/README.zh-CN.md; do
  for marker in \
    './scripts/run-acceptance-e2e.sh --disposable' \
    'contracts/acceptance-evidence.schema.json' \
    'tmp/acceptance/<run-id>/manifest.json' \
    'com.docker.compose.project' \
    'com.opencitadel.acceptance.project' \
    'com.opencitadel.acceptance.run' \
    'opencitadel.io/sandbox=true'; do
    require_marker "$f" "$marker" "deterministic acceptance"
  done
done
for f in scripts/README.md scripts/README.zh-CN.md \
  docs/operations/deployment.md docs/operations/deployment.zh-CN.md; do
  require_marker "$f" './scripts/run-acceptance-e2e.sh --disposable' \
    "deterministic acceptance entrypoint"
done
if rg -n 'PATROL_E2E|PATROL_E2E_ENABLED|patrol\.spec\.ts' \
  .github/workflows/ci.yml e2e/README.md e2e/README.zh-CN.md 2>/dev/null; then
  fail "found removed external-model Patrol E2E gate in current acceptance docs or CI"
fi

if [[ "$errors" -gt 0 ]]; then
  echo "Documentation check failed with $errors error(s)." >&2
  exit 1
fi

echo "Documentation checks passed."
