#!/usr/bin/env bash
set -euo pipefail

ACCEPTANCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ACCEPTANCE_ROOT"

if [[ -x "$ACCEPTANCE_ROOT/api/.venv/bin/python" ]]; then
  exec "$ACCEPTANCE_ROOT/api/.venv/bin/python" -m scripts.acceptance.runner "$@"
fi

exec uv run --project "$ACCEPTANCE_ROOT/api" python -m scripts.acceptance.runner "$@"
