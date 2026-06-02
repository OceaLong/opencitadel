#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
echo "Running Alembic migrations..."
python -m alembic upgrade head
echo "Migrations complete."
