#!/usr/bin/env bash
# OpenCitadel quickstart — get a working stack in ~10 minutes (BYO API key path).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}==>${NC} $*"; }
warn() { echo -e "${YELLOW}==>${NC} $*"; }
err()  { echo -e "${RED}==>${NC} $*" >&2; }

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "Missing required command: $1"
    exit 1
  fi
}

require_cmd docker
docker compose version >/dev/null 2>&1 || { err "Docker Compose v2 required"; exit 1; }

if [[ ! -f .env ]]; then
  info "Creating .env from .env.example ..."
  cp .env.example .env

  # Generate secrets
  if command -v openssl >/dev/null 2>&1; then
    API_SECRET="$(openssl rand -hex 32)"
    JWT_SECRET="$(openssl rand -hex 32)"
    SESSION_SECRET="$(openssl rand -hex 32)"
    sed -i.bak \
      -e "s|^API_KEY_SECRET=.*|API_KEY_SECRET=${API_SECRET}|" \
      -e "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" \
      -e "s|^SESSION_SECRET=.*|SESSION_SECRET=${SESSION_SECRET}|" \
      .env
    rm -f .env.bak
  fi

  # Sensible defaults for local HTTP quickstart
  if grep -q '^COOKIE_SECURE=' .env; then
    sed -i.bak 's|^COOKIE_SECURE=.*|COOKIE_SECURE=false|' .env
    rm -f .env.bak
  fi
  if grep -q '^FRONTEND_BASE_URL=' .env; then
    sed -i.bak 's|^FRONTEND_BASE_URL=.*|FRONTEND_BASE_URL=http://localhost:8088|' .env
    rm -f .env.bak
  fi
  if grep -q '^OAUTH_REDIRECT_BASE=' .env; then
    sed -i.bak 's|^OAUTH_REDIRECT_BASE=.*|OAUTH_REDIRECT_BASE=http://localhost:8088/api/auth/oauth|' .env
    rm -f .env.bak
  fi
  if grep -q '^ENV=' .env; then
    sed -i.bak 's|^ENV=.*|ENV=development|' .env
    rm -f .env.bak
  fi

  warn "Edit .env and set BOOTSTRAP_ADMIN_PASSWORD before continuing."
  warn "After first login, add your LLM API key in Settings → Models."
  read -r -p "Press Enter when .env is ready (or Ctrl+C to abort) ..."
else
  info ".env already exists — skipping generation"
fi

if [[ -z "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  # shellcheck disable=SC1091
  set -a && source .env && set +a
fi

if [[ -z "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  err "BOOTSTRAP_ADMIN_PASSWORD must be set in .env"
  exit 1
fi

PROFILE="${COMPOSE_PROFILES:-}"
if [[ -n "$PROFILE" ]]; then
  info "Using COMPOSE_PROFILES=${PROFILE} from .env"
  COMPOSE_CMD=(docker compose --profile "$PROFILE")
else
  COMPOSE_CMD=(docker compose)
fi

info "Building and starting OpenCitadel (this may take several minutes on first run) ..."
"${COMPOSE_CMD[@]}" up -d --build

info "Waiting for API health ..."
for i in $(seq 1 60); do
  if curl -sf "http://localhost:${NGINX_PORT:-8088}/api/status" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

PORT="${NGINX_PORT:-8088}"
echo ""
info "OpenCitadel is starting."
echo ""
echo "  URL:      http://localhost:${PORT}"
echo "  Login:    ${BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}"
echo "  Password: (your BOOTSTRAP_ADMIN_PASSWORD from .env)"
echo ""
echo "Next steps:"
echo "  1. Log in and open Settings → Models"
echo "  2. Add an OpenAI / Anthropic / compatible API key"
echo "  3. Start a new Agent session from the home page"
echo ""
echo "For fully offline mode (Ollama + MinIO):"
echo "  COMPOSE_PROFILES=local STORAGE_PROVIDER=minio in .env, then re-run this script"
echo ""
info "Logs: ${COMPOSE_CMD[*]} logs -f opencitadel-api opencitadel-worker"
