#!/usr/bin/env bash
# OpenCitadel quickstart — get a working stack in ~10 minutes (BYO API key path).
# Pass --demo to additionally bring up the Ops Patrol demo profiles
# (opencitadel-ops-collector + ops-console) and seed a runnable demo Pack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEMO_MODE=0
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO_MODE=1 ;;
  esac
done

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

wait_for_container_health() {
  local container="$1"
  for i in $(seq 1 60); do
    local status
    status="$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || echo "unknown")"
    [[ "$status" == "healthy" ]] && return 0
    sleep 5
  done
  return 1
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
    METRICS_TOKEN="$(openssl rand -hex 32)"
    sed -i.bak \
      -e "s|^API_KEY_SECRET=.*|API_KEY_SECRET=${API_SECRET}|" \
      -e "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" \
      -e "s|^SESSION_SECRET=.*|SESSION_SECRET=${SESSION_SECRET}|" \
      -e "s|^METRICS_TOKEN=.*|METRICS_TOKEN=${METRICS_TOKEN}|" \
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
  # Local-first defaults: bundled MinIO for file uploads (override for cloud COS)
  if grep -q '^COMPOSE_PROFILES=' .env; then
    if [[ "$DEMO_MODE" -eq 1 ]]; then
      sed -i.bak 's|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=local,patrol,demo|' .env
    else
      sed -i.bak 's|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=local|' .env
    fi
    rm -f .env.bak
  fi
  if grep -q '^STORAGE_PROVIDER=' .env; then
    sed -i.bak 's|^STORAGE_PROVIDER=.*|STORAGE_PROVIDER=minio|' .env
    rm -f .env.bak
  fi

  warn "Edit .env and set BOOTSTRAP_ADMIN_PASSWORD before continuing."
  warn "After first login, add your LLM API key in Settings → Models."
  warn "Quickstart defaults: COMPOSE_PROFILES=local + STORAGE_PROVIDER=minio (see .env for cloud COS)."
  if [[ "$DEMO_MODE" -eq 1 ]]; then
    warn "To auto-register a demo LLM instead, set DEMO_LLM_BASE_URL/API_KEY/MODEL in .env now —"
    warn "this pause is the only time before containers start (compose reads .env at startup)."
  fi
  if [[ -n "${QUICKSTART_NONINTERACTIVE:-}" ]] || [[ ! -t 0 ]]; then
    info "Non-interactive mode — continuing without prompt."
  else
    read -r -p "Press Enter when .env is ready (or Ctrl+C to abort) ..."
  fi
else
  info ".env already exists — skipping generation"
  # Idempotent merge, not overwrite: a pre-existing .env may already carry a
  # customized COMPOSE_PROFILES (e.g. empty, for the cloud/COS path). Running
  # with --demo against that .env must still land local/patrol/demo in the
  # *persisted* file — not just in this run's in-memory $PROFILE below — so
  # that a later bare `docker compose ...` (e.g. tutorial step 4's
  # `docker compose stop ops-console`, which has no --profile flags of its
  # own and relies entirely on .env) sees the demo services too.
  if [[ "$DEMO_MODE" -eq 1 ]] && grep -q '^COMPOSE_PROFILES=' .env; then
    _existing_profiles="$(grep '^COMPOSE_PROFILES=' .env | head -n1 | cut -d'=' -f2-)"
    _merged_profiles="$_existing_profiles"
    IFS=',' read -ra _existing_profile_list <<< "$_existing_profiles"
    for _want_profile in local patrol demo; do
      _profile_found=0
      for _have_profile in "${_existing_profile_list[@]:-}"; do
        _have_profile="$(echo "$_have_profile" | xargs)"
        if [[ "$_have_profile" == "$_want_profile" ]]; then
          _profile_found=1
          break
        fi
      done
      if [[ "$_profile_found" -eq 0 ]]; then
        _merged_profiles="${_merged_profiles:+$_merged_profiles,}$_want_profile"
      fi
    done
    if [[ "$_merged_profiles" != "$_existing_profiles" ]]; then
      sed -i.bak "s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=${_merged_profiles}|" .env
      rm -f .env.bak
      info "Merged COMPOSE_PROFILES=${_merged_profiles} into existing .env for --demo (was '${_existing_profiles}')."
    fi
  fi
fi

if [[ -z "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  # Intentionally NOT `set -a` (export): exporting re-parses every .env value
  # as bash syntax and re-exposes it to child processes, including the
  # `docker compose` invocations below. bash's own quote handling silently
  # strips embedded double quotes from values like
  # OPS_COLLECTOR_ALLOWED_NAMESPACES=["opencitadel"] (JSON) when exported
  # this way, and an exported shell variable then outranks .env in Compose's
  # own (correct) interpolation, feeding the Collector invalid JSON and
  # crash-looping it. A plain, unexported `source` still lets this script
  # read the values below via `${VAR}`, while leaving `docker compose` free
  # to read `.env` itself with its own dotenv parser.
  # shellcheck disable=SC1091
  source .env
fi

if [[ -z "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  err "BOOTSTRAP_ADMIN_PASSWORD must be set in .env"
  exit 1
fi

PROFILE="${COMPOSE_PROFILES:-}"
if [[ "$DEMO_MODE" -eq 1 ]]; then
  # Merge in the demo profiles without discarding whatever the operator's
  # own .env already sets (COMPOSE_PROFILES may be empty for the cloud/COS
  # path, or already customized) — patrol/demo just need to be present.
  PROFILE="${PROFILE:+$PROFILE,}patrol,demo"
fi

COMPOSE_CMD=(docker compose)
if [[ -n "$PROFILE" ]]; then
  info "Using COMPOSE_PROFILES=${PROFILE}$( [[ "$DEMO_MODE" -eq 1 ]] && echo " (--demo)" )"
  # Split on commas into one `--profile X` flag each. A single profile (the
  # common, pre-existing case) produces the exact same invocation as before
  # this option existed; docker compose tolerates a profile listed more than
  # once (harmless), so no dedup is needed. Avoids `declare -A`, which the
  # bash 3.2 shipped as /bin/bash on macOS does not support.
  IFS=',' read -ra _profile_list <<< "$PROFILE"
  for p in "${_profile_list[@]}"; do
    p="$(echo "$p" | xargs)"
    [[ -z "$p" ]] && continue
    COMPOSE_CMD+=(--profile "$p")
  done
fi

info "Building sandbox image (required for dynamic Agent tool execution) ..."
docker compose build opencitadel-sandbox

info "Building and starting OpenCitadel (this may take several minutes on first run) ..."
"${COMPOSE_CMD[@]}" up -d --build

info "Waiting for API health ..."
for i in $(seq 1 60); do
  if curl -sf "http://localhost:${NGINX_PORT:-8088}/api/status" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

if [[ "$DEMO_MODE" -eq 1 ]]; then
  info "Waiting for the Ops Patrol demo containers (ops-collector, ops-console) ..."
  wait_for_container_health opencitadel-ops-collector \
    || warn "opencitadel-ops-collector did not report healthy in time; seeding below may fail."
  wait_for_container_health opencitadel-ops-console \
    || warn "opencitadel-ops-console did not report healthy in time; seeding below may fail."

  info "Seeding Ops Patrol demo data (feature flag, Collector policies, demo Pack) ..."
  if "${COMPOSE_CMD[@]}" exec -T opencitadel-api python -m app.seed_demo; then
    info "Demo data seeded."
  else
    err "Demo data seeding failed — see the output above for details. Retry with:"
    err "  ${COMPOSE_CMD[*]} exec -T opencitadel-api python -m app.seed_demo"
  fi
fi

PORT="${NGINX_PORT:-8088}"
echo ""
info "OpenCitadel is starting."
echo ""
echo "  URL:      http://localhost:${PORT}"
echo "  Login:    ${BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}"
echo "  Password: (your BOOTSTRAP_ADMIN_PASSWORD from .env)"
echo ""
echo "Next steps:"
if [[ "$DEMO_MODE" -eq 1 ]]; then
  echo "  1. Log in, open 'Ops Patrol', and run the seeded 'Demo Governance Patrol' Pack"
  echo "  2. Open http://localhost:${PORT}/admin/governance for the governance dashboard"
  echo "  3. To manufacture a Finding: docker compose stop ops-console, then Run now again"
  echo "     (docker compose start ops-console restores it before the next demo)"
  echo ""
  echo "See the 'python -m app.seed_demo' output above for the full walkthrough, including"
  echo "why the demo Finding comes from the demo-console-health check specifically."
else
  echo "  1. Log in and open Settings → Models"
  echo "  2. Add an OpenAI / Anthropic / compatible API key"
  echo "  3. Start a new Agent session from the home page"
fi
echo ""
echo "For cloud object storage instead of bundled MinIO:"
echo "  Set COMPOSE_PROFILES= (empty) and STORAGE_PROVIDER=cos + COS_* in .env, then re-run"
echo ""
echo "For fully offline LLM (Ollama on host):"
echo "  Keep COMPOSE_PROFILES=local STORAGE_PROVIDER=minio; install Ollama and add endpoint in Settings"
echo ""
info "Logs: ${COMPOSE_CMD[*]} logs -f opencitadel-api opencitadel-worker"
