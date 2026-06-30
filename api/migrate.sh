#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
echo "Running database migrations and config data migrations..."
python -m app.migrate
echo "Migrations complete."
