#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

reset_data=false
if [[ "${1:-}" == "--reset-data" ]]; then
  reset_data=true
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--reset-data]" >&2
  exit 2
fi

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 is required" >&2; exit 1; }

if [[ ! -f .env ]]; then
  cp .env.example .env
  if command -v openssl >/dev/null; then
    replace_line() {
      local key="$1" value="$2"
      sed -i.bak "s|^${key}=.*|${key}=${value}|" .env
      rm -f .env.bak
    }
    replace_line ENV development
    replace_line COMPOSE_PROFILES local
    replace_line STORAGE_PROVIDER minio
    replace_line COOKIE_SECURE false
    replace_line FRONTEND_BASE_URL http://localhost:8088
    replace_line OAUTH_REDIRECT_BASE http://localhost:8088/api/auth/oauth
    replace_line API_KEY_SECRET "$(openssl rand -hex 32)"
    replace_line AUDIT_SIGNING_KEY "$(openssl rand -hex 32)"
    replace_line JWT_SECRET "$(openssl rand -hex 32)"
    replace_line SESSION_SECRET "$(openssl rand -hex 32)"
    replace_line DATABASE_AUTHORIZATION_SIGNING_SECRET "$(openssl rand -hex 32)"
    replace_line SANDBOX_BROKER_TOKEN "$(openssl rand -hex 32)"
    replace_line SANDBOX_TOKEN_SEED "$(openssl rand -hex 32)"
    replace_line BOOTSTRAP_ADMIN_PASSWORD "$(openssl rand -hex 16)"
    replace_line POSTGRES_ADMIN_PASSWORD "$(openssl rand -hex 24)"
    replace_line POSTGRES_MIGRATION_PASSWORD "$(openssl rand -hex 24)"
    replace_line POSTGRES_PASSWORD "$(openssl rand -hex 24)"
    replace_line POSTGRES_KERNEL_PASSWORD "$(openssl rand -hex 24)"
    replace_line REDIS_PASSWORD "$(openssl rand -hex 24)"
  else
    echo "created .env; replace every placeholder before starting" >&2
    exit 1
  fi
fi

compose_project="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env | head -n 1)"
compose_project="${compose_project:-opencitadel}"
sandbox_network="$(sed -n 's/^SANDBOX_NETWORK=//p' .env | head -n 1)"
export SANDBOX_NETWORK="${sandbox_network:-${compose_project}_opencitadel-sandbox-network}"

if [[ "$reset_data" == true ]]; then
  echo "removing OpenCitadel containers and named volumes"
  docker compose --project-name "$compose_project" --profile local down --volumes --remove-orphans
fi

docker compose --project-name "$compose_project" --profile local build opencitadel-sandbox
docker compose --project-name "$compose_project" --profile local up -d --build

port="$(sed -n 's/^NGINX_PORT=//p' .env | head -n 1)"
port="${port:-8088}"
for _attempt in $(seq 1 60); do
  if curl -fsS "http://localhost:${port}/api/health/ready" >/dev/null 2>&1; then
    echo "OpenCitadel is ready at http://localhost:${port}"
    exit 0
  fi
  sleep 5
done

echo "startup timed out; inspect: docker compose --project-name $compose_project logs" >&2
exit 1
