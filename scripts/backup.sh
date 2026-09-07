#!/usr/bin/env bash
# A new output directory is required. Existing backups are never overwritten.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/backup_tool.py" backup "${1:-backups/$(date +%Y%m%d-%H%M%S)}"
