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

echo "==> Checking docs/ bilingual pairs ..."
while IFS= read -r -d '' en; do
  base="$(basename "$en")"
  case "$base" in
    README.md|MAINTENANCE_CHECKLIST.md) continue ;;
  esac
  check_pair "$en"
done < <(find docs/architecture docs/operations docs/tutorials -name '*.md' ! -name '*.zh-CN.md' -print0 2>/dev/null)

echo "==> Checking reverse pairs ..."
while IFS= read -r -d '' zh; do
  en="${zh%.zh-CN.md}.md"
  if [[ ! -f "$en" ]]; then
    fail "orphan Chinese doc without English pair: $zh (expected $en)"
  fi
done < <(find docs/architecture docs/operations docs/tutorials -name '*.zh-CN.md' -print0 2>/dev/null)

echo "==> Checking tutorial 05 in indexes ..."
for f in docs/README.md docs/README.zh-CN.md README.md README.zh-CN.md; do
  if ! grep -q '05-refund-reconciliation-compliance' "$f"; then
    fail "tutorial 05 not listed in $f"
  fi
done

echo "==> Checking stale /settings/integrations references in docs ..."
if rg -n '/settings/integrations' docs README.md README.zh-CN.md ui/README.md ui/README.zh-CN.md \
  --glob '!MAINTENANCE_CHECKLIST*.md' 2>/dev/null; then
  fail "found stale /settings/integrations route in documentation (use Settings modal → Integrations tab)"
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
