#!/usr/bin/env bash
# Start the local Compose stack; --demo additionally seeds the Ops demonstration.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=configure-local.sh
source "$ROOT/scripts/configure-local.sh"

DEMO_MODE=0
for argument in "$@"; do
  case "$argument" in
    --demo) DEMO_MODE=1 ;;
    *) printf 'ERROR: Unknown option: %s\n' "$argument" >&2; exit 1 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { printf '%s\n' 'ERROR: Docker is required.' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { printf '%s\n' 'ERROR: Docker Compose v2 is required.' >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  printf '%s\n' 'ERROR: Docker daemon is unavailable. Start Docker Desktop or Docker Engine, then retry make quickstart.' >&2
  exit 1
}

configure_local_env "$ROOT" "$DEMO_MODE"
if [[ "$CONFIG_CREATED" -eq 1 ]]; then
  printf '%s\n' 'Created private .env with persistent installation credentials.'
else
  printf 'Using %s; stored installation credentials are preserved.\n' "$CONFIG_FILE"
fi
configure_compose_interpolation
configure_local_site
configure_docker_socket_group

COMPOSE_PROJECT="$(config_value COMPOSE_PROJECT_NAME)"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-opencitadel}"
if [[ ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9-]{2,47}$ ]]; then
  printf '%s\n' 'ERROR: COMPOSE_PROJECT_NAME must match ^[a-z0-9][a-z0-9-]{2,47}$' >&2
  exit 1
fi
SANDBOX_NETWORK="$(config_value SANDBOX_NETWORK)"
SANDBOX_NETWORK="${SANDBOX_NETWORK:-${COMPOSE_PROJECT}_opencitadel-sandbox-network}"
export SANDBOX_NETWORK

PROFILE="$(config_value COMPOSE_PROFILES)"
if [[ "$DEMO_MODE" -eq 1 ]]; then
  for wanted in local patrol demo; do
    case ",$PROFILE," in *",$wanted,"*) ;; *) PROFILE="${PROFILE:+$PROFILE,}$wanted" ;; esac
  done
fi
COMPOSE_CMD=(docker compose --project-name "$COMPOSE_PROJECT")
COMPOSE_CMD+=(--env-file "$CONFIG_FILE")
IFS=',' read -ra profiles <<< "$PROFILE"
for profile in "${profiles[@]:-}"; do
  profile="${profile// /}"
  [[ -z "$profile" ]] || COMPOSE_CMD+=(--profile "$profile")
done

WAIT_TIMEOUT="$(config_value QUICKSTART_WAIT_TIMEOUT_SECONDS)"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-300}"
if [[ ! "$WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  printf '%s\n' 'ERROR: QUICKSTART_WAIT_TIMEOUT_SECONDS must be a positive integer.' >&2
  exit 1
fi

diagnostics() {
  printf '%s\n' 'Startup did not reach readiness. Owned service state and recent logs:' >&2
  "${COMPOSE_CMD[@]}" ps --all >&2 || true
  "${COMPOSE_CMD[@]}" logs --tail 80 opencitadel-migrate opencitadel-api \
    opencitadel-execution-kernel opencitadel-nginx >&2 || true
}

printf '%s\n' 'Building the dynamic sandbox image ...'
if ! "${COMPOSE_CMD[@]}" build opencitadel-sandbox; then
  diagnostics
  exit 1
fi
printf '%s\n' 'Building the stack and waiting for service readiness ...'
if ! "${COMPOSE_CMD[@]}" up -d --build --wait --wait-timeout "$WAIT_TIMEOUT"; then
  diagnostics
  if [[ -n "$CONFIG_GENERATED_ADMIN_PASSWORD" ]]; then
    printf '%s\n' 'Generated login credentials remain saved in .env; retrying preserves them.' >&2
  fi
  exit 1
fi

if [[ "$DEMO_MODE" -eq 1 ]]; then
  printf '%s\n' 'Seeding the Ops demonstration ...'
  if ! "${COMPOSE_CMD[@]}" exec -T opencitadel-api python -m app.seed_demo; then
    printf '%s\n' 'ERROR: Demo seeding failed; the stack is running. Retry bash scripts/quickstart.sh --demo.' >&2
    exit 1
  fi
fi

config_consume_initial_login
printf '\nOpenCitadel is ready.\n  URL: %s\n' "$FRONTEND_BASE_URL"
ADMIN_EMAIL="$(config_value BOOTSTRAP_ADMIN_EMAIL)"
if [[ -n "$ADMIN_EMAIL" ]]; then printf '  Login: %s\n' "$ADMIN_EMAIL"; fi
if [[ -n "$CONFIG_INITIAL_ADMIN_PASSWORD" ]]; then
  printf '  First administrator password: %s\n' "$CONFIG_INITIAL_ADMIN_PASSWORD"
else
  printf '  Password: stored BOOTSTRAP_ADMIN_PASSWORD in %s (or your configured identity provider).\n' "$CONFIG_FILE"
fi
printf '%s\n' 'After login, add an endpoint, model and chat binding in Settings → Inference.'
printf '%s\n' 'Optional deployment settings: deploy/config/.env.reference'
