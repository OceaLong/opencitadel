#!/usr/bin/env bash
# Lightweight documentation consistency checks.
set -euo pipefail

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

echo "==> Checking docs/ bilingual pairs ..."
check_pair_dir docs/architecture
check_pair_dir docs/operations
check_pair_dir docs/tutorials
check_pair docs/DOCUMENTATION_INVENTORY.md

echo "==> Checking module README bilingual pairs ..."
for en in README.md api/README.md ui/README.md sandbox/README.md nginx/README.md \
  deploy/helm/opencitadel/README.md demo/ops-console/README.md \
  e2e/README.md scripts/README.md deploy/scripts/README.md; do
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

echo "==> Checking new architecture docs in indexes ..."
for topic in llm-endpoints-and-models frontend-ui task-recovery technical-decisions knowledge-base-ingestion; do
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
  docs/tutorials/02-internal-knowledge-base.zh-CN.md 2>/dev/null | rg -v '50 MB|50\s*MB|gateway|网关|nginx|CODEBASE'; then
  fail "KB tutorial should not imply 200MB document limit without 50MB AppConfig caveat"
fi

echo "==> Checking stale /settings/integrations references in docs ..."
if rg -n '/settings/integrations' docs README.md README.zh-CN.md ui/README.md ui/README.zh-CN.md \
  --glob '!MAINTENANCE_CHECKLIST*.md' 2>/dev/null; then
  fail "found stale /settings/integrations route in documentation (use Settings modal → Integrations tab)"
fi

echo "==> Checking stale checkpoint rollback API path ..."
if rg -n 'checkpoints/\{[^}]+\}/rollback' docs/architecture/checkpoints-and-hitl.md \
  docs/architecture/checkpoints-and-hitl.zh-CN.md 2>/dev/null; then
  fail "checkpoint API should use /restore not /rollback"
fi

echo "==> Checking stale /admin/usage UI route in docs ..."
if rg -n '\| `/admin/usage` \|' docs/architecture/admin-auditor-compliance.md \
  docs/architecture/admin-auditor-compliance.zh-CN.md 2>/dev/null; then
  fail "UI usage charts are on /admin overview, not /admin/usage"
fi

echo "==> Checking four-images Helm wording ..."
if rg -n 'four images|四镜像' docs/operations/deployment.md docs/operations/deployment.zh-CN.md 2>/dev/null; then
  fail "deployment docs still mention four images; should be five (api/worker/migrate/ui/sandbox)"
fi

if [[ "$errors" -gt 0 ]]; then
  echo "Documentation check failed with $errors error(s)." >&2
  exit 1
fi

echo "Documentation checks passed."
