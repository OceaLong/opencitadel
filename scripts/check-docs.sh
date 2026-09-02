#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for english in \
  README.md \
  api/README.md \
  ui/README.md \
  sandbox/README.md \
  scripts/README.md \
  deploy/helm/opencitadel/README.md \
  docs/architecture/kernel-v2.md \
  docs/operations/deployment.md; do
  chinese="${english%.md}.zh-CN.md"
  [[ -f "$english" ]] || { echo "missing $english" >&2; exit 1; }
  [[ -f "$chinese" ]] || { echo "missing $chinese" >&2; exit 1; }
done

if rg -n 'ops-collector|ops-actuator|patrol|compliance|/sessions|scheduled-job' \
  docker-compose.yml Makefile .github/workflows deploy/helm scripts \
  --glob '!README*' \
  --glob '!check-docs.sh' \
  --glob '!prune-pre-v2.sh' \
  --glob '!test-prune-pre-v2.sh'; then
  echo "retired product surface remains in executable/deployment files" >&2
  exit 1
fi

echo "documentation and retired-surface contracts passed"
