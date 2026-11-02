#!/usr/bin/env bash
# Local configuration initialization. Source this script, never the dotenv file.

CONFIG_SECRET_KEYS=(
  API_KEY_SECRET AUDIT_SIGNING_KEY JWT_SECRET SESSION_SECRET
  SANDBOX_BROKER_TOKEN SANDBOX_TOKEN_SEED PUBLIC_CURSOR_SECRET
  DATABASE_AUTHORIZATION_SIGNING_SECRET METRICS_TOKEN
  POSTGRES_ADMIN_PASSWORD POSTGRES_MIGRATION_PASSWORD POSTGRES_PASSWORD
  POSTGRES_KERNEL_PASSWORD REDIS_PASSWORD MINIO_SECRET_KEY
  OPS_COLLECTOR_TOKEN OPS_ACTUATOR_TOKEN BOOTSTRAP_ADMIN_PASSWORD
)

dotenv_read() {
  local file="$1" key="$2"
  # Read the small scalar controls needed by the launcher without interpreting
  # shell code, dollar expansions or command substitutions. Other dotenv values
  # (including JSON) remain byte-for-byte in the file for Compose's own parser.
  awk -v wanted="$key" '
    {
      line=$0; sub(/\r$/, "", line); sub(/^[ \t]+/, "", line)
      sub(/^export[ \t]+/, "", line)
      if (line !~ "^" wanted "[ \t]*=") next
      sub("^" wanted "[ \t]*=[ \t]*", "", line)
      quote=substr(line,1,1)
      if (quote == "\047" || quote == "\042") {
        value=""; escaped=0
        for (i=2; i<=length(line); i++) {
          char=substr(line,i,1)
          if (escaped) {
            if (char == quote || char == "\\") value=value char
            else value=value "\\" char
            escaped=0
          } else if (char == "\\") {
            # Compose single quotes only unescape an apostrophe, preserving
            # every other backslash. Double quotes also unescape backslashes.
            if (quote == "\047" && substr(line,i+1,1) != quote) value=value char
            else escaped=1
          }
          else if (char == quote) break
          else value=value char
        }
        if (quote == "\042") gsub(/\$\$/, "$", value)
      } else {
        value=line; sub(/[ \t]+#.*/, "", value); sub(/[ \t]+$/, "", value)
      }
      found=1; result=value
    }
    END {if(found) printf "%s", result}
  ' "$file"
}

config_value() {
  local key="$1" value
  # Unlike scalar defaults, an explicitly empty profile list disables optional
  # services. Preserve Compose's distinction between unset and empty here.
  if [[ "$key" == COMPOSE_PROFILES && "${COMPOSE_PROFILES+x}" == x ]]; then
    printf '%s' "$COMPOSE_PROFILES"
    return
  fi
  value="${!key:-}"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  elif [[ -n "${CONFIG_RESOLVED_CONTROLS:-}" ]] &&
       printf '%s\n' "$CONFIG_RESOLVED_CONTROLS" | awk -v key="$key" '
         index($0, key "=") == 1 {found=1}
         END {exit !found}
       '; then
    printf '%s\n' "$CONFIG_RESOLVED_CONTROLS" | awk -v key="$key" '
      index($0, key "=") == 1 {printf "%s", substr($0,length(key)+2)}
    '
  else
    dotenv_read "$CONFIG_FILE" "$key"
  fi
}

configure_compose_interpolation() {
  local key requires_compose=0
  for key in COMPOSE_PROJECT_NAME COMPOSE_PROFILES FRONTEND_BASE_URL NGINX_PORT \
    NGINX_HTTPS_PORT OPENCITADEL_DOMAIN HTTPS_ENABLED SANDBOX_NETWORK \
    DOCKER_SOCK_GID QUICKSTART_WAIT_TIMEOUT_SECONDS BOOTSTRAP_ADMIN_EMAIL; do
    [[ "$(config_value "$key")" != *'$'* ]] || requires_compose=1
  done
  [[ "$requires_compose" -eq 1 ]] || return 0
  # Let Compose evaluate its own dotenv interpolation rules, including single
  # quotes and default operators. Filter to launcher controls before capture;
  # the environment table (and its secrets) is never emitted to the terminal.
  if ! CONFIG_RESOLVED_CONTROLS="$(
    docker compose --env-file "$CONFIG_FILE" config --environment | awk '
      /^(COMPOSE_PROJECT_NAME|COMPOSE_PROFILES|FRONTEND_BASE_URL|NGINX_PORT|NGINX_HTTPS_PORT|OPENCITADEL_DOMAIN|HTTPS_ENABLED|SANDBOX_NETWORK|DOCKER_SOCK_GID|QUICKSTART_WAIT_TIMEOUT_SECONDS|BOOTSTRAP_ADMIN_EMAIL)=/
    '
  )"; then
    printf '%s\n' 'ERROR: Compose could not resolve the selected dotenv configuration.' >&2
    return 1
  fi
}

config_random() {
  local value=""
  if command -v openssl >/dev/null 2>&1; then
    value="$(openssl rand -hex 32 2>/dev/null)" || value=""
  fi
  if [[ ! "$value" =~ ^[0-9a-f]{64}$ ]] && [[ -r /dev/urandom ]]; then
    value="$(LC_ALL=C od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  fi
  if [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
    printf '%s\n' 'ERROR: Cannot obtain secure random bytes (openssl or /dev/urandom required).' >&2
    return 1
  fi
  printf '%s' "$value"
}

config_write_value() {
  local file="$1" key="$2" value="$3" escaped
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    printf 'ERROR: %s must be a single-line dotenv value.\n' "$key" >&2
    return 1
  fi
  # Single quotes keep dollar expressions literal. Compose preserves ordinary
  # backslashes inside them, so only escape apostrophes. A trailing backslash or
  # one immediately before an apostrophe confuses Compose's quote boundary;
  # use its double-quoted encoding for those values, escaping dollars as $$.
  if [[ "$value" == *\\ || "$value" == *"\\'"* ]]; then
    escaped="$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/$$/g')"
    printf '%s="%s"\n' "$key" "$escaped" >> "$file"
  else
    escaped="$(printf '%s' "$value" | sed -e "s/'/\\\\'/g")"
    printf "%s='%s'\n" "$key" "$escaped" >> "$file"
  fi
}

config_login_fingerprint() {
  # A non-secret change detector prevents printing a password replaced by an
  # operator between configuration and startup. This is not an auth checksum.
  printf '%s' "$1" | cksum | awk '{print $1 ":" $2}'
}

config_consume_initial_login() {
  local pending password fingerprint temporary
  CONFIG_INITIAL_ADMIN_PASSWORD=""
  # Never replace a symlink or write through it merely to update launcher state.
  [[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]] || return 0
  pending="$(awk '
    /^# opencitadel-initial-admin-login-pending: / {
      sub(/^# opencitadel-initial-admin-login-pending: /, ""); value=$0
    }
    END {printf "%s", value}
  ' "$CONFIG_FILE")"
  [[ -n "$pending" ]] || return 0
  password="$(dotenv_read "$CONFIG_FILE" BOOTSTRAP_ADMIN_PASSWORD)"
  fingerprint="$(config_login_fingerprint "$password")"
  # Supplied or subsequently edited passwords are never disclosed. A marker
  # without its original random password is consumed without printing a value.
  if [[ "$pending" == "$fingerprint" && "$password" =~ ^[0-9a-f]{64}$ &&
        -z "${BOOTSTRAP_ADMIN_PASSWORD:-}" &&
        -n "$(dotenv_read "$CONFIG_FILE" BOOTSTRAP_ADMIN_EMAIL)" ]]; then
    CONFIG_INITIAL_ADMIN_PASSWORD="$password"
  fi
  temporary="$(umask 077; mktemp "${CONFIG_FILE}.tmp.XXXXXX")" || return 1
  cp -p "$CONFIG_FILE" "$temporary" || { rm -f "$temporary"; return 1; }
  awk '!/^# opencitadel-initial-admin-login-pending: /' "$CONFIG_FILE" > "$temporary" \
    || { rm -f "$temporary"; return 1; }
  mv "$temporary" "$CONFIG_FILE"
}

config_replace_value() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp "${CONFIG_FILE}.tmp.XXXXXX")" || return 1
  cp -p "$CONFIG_FILE" "$temporary" || { rm -f "$temporary"; return 1; }
  awk -v key="$key" -v value="$value" '
    $0 ~ "^[ \t]*(export[ \t]+)?" key "[ \t]*=" {print key "=" value; found=1; next}
    {print}
    END {if(!found) print key "=" value}
  ' "$CONFIG_FILE" > "$temporary" || { rm -f "$temporary"; return 1; }
  mv "$temporary" "$CONFIG_FILE"
}

configure_local_env() {
  local repository="$1" demo_mode="${2:-0}" temporary key value profiles wanted present selected
  selected="${OPENCITADEL_ENV_FILE:-}"
  if [[ -z "$selected" && -f "$repository/.env" ]]; then
    selected="$(dotenv_read "$repository/.env" OPENCITADEL_ENV_FILE)"
  fi
  case "$selected" in
    ''|.env|./.env|.env.example|./.env.example) CONFIG_FILE="$repository/.env" ;;
    /*) CONFIG_FILE="$selected" ;;
    *) CONFIG_FILE="$repository/$selected" ;;
  esac
  CONFIG_CREATED=0
  CONFIG_RESOLVED_CONTROLS=""
  CONFIG_GENERATED_ADMIN_PASSWORD=""
  if [[ ! -e "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]]; then
    if [[ "$CONFIG_FILE" != "$repository/.env" ]]; then
      printf 'ERROR: Selected configuration does not exist: %s\n' "$CONFIG_FILE" >&2
      return 1
    fi
    temporary="$(umask 077; mktemp "${CONFIG_FILE}.tmp.XXXXXX")" || return 1
    awk '
      /^[A-Z][A-Z0-9_]*=/ {
        key=$0; sub(/=.*/, "", key)
        if (key != "OPENCITADEL_ENV_FILE" && key in ENVIRON &&
            (ENVIRON[key] != "" || key == "COMPOSE_PROFILES")) {
          print key "=" ENVIRON[key]; next
        }
      }
      {print}
    ' "$repository/.env.example" > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" || { rm -f "$temporary"; return 1; }
    for key in "${CONFIG_SECRET_KEYS[@]}"; do
      value="${!key:-}"
      if [[ -z "$value" ]]; then
        value="$(config_random)" || { rm -f "$temporary"; return 1; }
        if [[ "$key" == BOOTSTRAP_ADMIN_PASSWORD ]]; then CONFIG_GENERATED_ADMIN_PASSWORD="$value"; fi
      fi
      config_write_value "$temporary" "$key" "$value" || { rm -f "$temporary"; return 1; }
    done
    config_write_value "$temporary" BOOTSTRAP_ADMIN_EMAIL "${BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}" \
      || { rm -f "$temporary"; return 1; }
    if [[ -n "$CONFIG_GENERATED_ADMIN_PASSWORD" ]]; then
      printf '# opencitadel-initial-admin-login-pending: %s\n' \
        "$(config_login_fingerprint "$CONFIG_GENERATED_ADMIN_PASSWORD")" >> "$temporary"
    fi
    # A hard link installs atomically without replacing a concurrently created
    # configuration. The temporary file stays private throughout initialization.
    if ! ln "$temporary" "$CONFIG_FILE"; then
      rm -f "$temporary"
      printf '%s\n' 'ERROR: .env appeared during initialization; preserving it. Run quickstart again.' >&2
      return 1
    fi
    rm -f "$temporary"
    CONFIG_CREATED=1
  elif [[ ! -f "$CONFIG_FILE" ]]; then
    printf '%s\n' 'ERROR: .env must be a regular dotenv file.' >&2
    return 1
  else
    # An existing installation may rely on derived signing keys and old DB
    # credentials. Never manufacture new secrets or replace any stored values.
    present=0
    for key in "${CONFIG_SECRET_KEYS[@]}"; do
      [[ -z "$(config_value "$key")" ]] || present=1
    done
    if [[ "$present" -eq 0 ]]; then
      printf '%s\n' 'ERROR: Existing .env has no installation secrets. It will not be regenerated.' >&2
      printf '%s\n' 'For a fresh installation, move this unused template aside and run make quickstart again.' >&2
      printf '%s\n' 'For an existing database, restore its original .env and encryption/signing credentials.' >&2
      return 1
    fi
  fi

  value="$(dotenv_read "$CONFIG_FILE" OPENCITADEL_ENV_FILE)"
  if [[ "$value" == .env.example || "$value" == ./.env.example ]]; then
    if [[ -L "$CONFIG_FILE" ]]; then
      printf '%s\n' 'ERROR: Update OPENCITADEL_ENV_FILE to .env in the linked configuration.' >&2
      return 1
    fi
    config_replace_value OPENCITADEL_ENV_FILE .env || return 1
  fi

  if [[ "$demo_mode" -eq 1 ]]; then
    profiles="$(dotenv_read "$CONFIG_FILE" COMPOSE_PROFILES)"
    profiles="${profiles// /}"
    for wanted in local patrol demo; do
      case ",$profiles," in *",$wanted,"*) ;; *) profiles="${profiles:+$profiles,}$wanted" ;; esac
    done
    config_replace_value COMPOSE_PROFILES "$profiles" || return 1
  fi
  # Match Compose interpolation and service env_file loading, including an
  # existing explicitly selected advanced configuration.
  OPENCITADEL_ENV_FILE="$CONFIG_FILE"
  export OPENCITADEL_ENV_FILE
}

configure_local_site() {
  local url scheme host port explicit_http explicit_https
  url="$(config_value FRONTEND_BASE_URL)"
  explicit_http="$(config_value NGINX_PORT)"
  explicit_https="$(config_value NGINX_HTTPS_PORT)"
  url="${url:-http://localhost:${explicit_http:-8088}}"
  if [[ ! "$url" =~ ^(http|https)://(\[[^]]+\]|[a-zA-Z0-9._-]+)(:([0-9]+))?/?$ ]]; then
    printf '%s\n' 'ERROR: FRONTEND_BASE_URL must be an HTTP(S) origin without a path, query or userinfo.' >&2
    return 1
  fi
  scheme="${BASH_REMATCH[1]}"; host="${BASH_REMATCH[2]}"; port="${BASH_REMATCH[4]}"
  if [[ "$scheme" == https ]]; then port="${port:-443}"; else port="${port:-80}"; fi
  port="$(printf '%s' "$port" | sed 's/^0*//')"; port="${port:-0}"
  if [[ ${#port} -gt 5 || "$port" -lt 1 || "$port" -gt 65535 ]]; then
    printf '%s\n' 'ERROR: FRONTEND_BASE_URL port must be between 1 and 65535.' >&2
    return 1
  fi
  FRONTEND_BASE_URL="${url%/}"
  if [[ "$scheme" == https ]]; then
    NGINX_HTTPS_PORT="${explicit_https:-$port}"
    NGINX_PORT="${explicit_http:-80}"
  else
    NGINX_PORT="${explicit_http:-$port}"
    NGINX_HTTPS_PORT="${explicit_https:-8443}"
  fi
  OPENCITADEL_DOMAIN="$(config_value OPENCITADEL_DOMAIN)"
  OPENCITADEL_DOMAIN="${OPENCITADEL_DOMAIN:-${host#[}}"; OPENCITADEL_DOMAIN="${OPENCITADEL_DOMAIN%]}"
  HTTPS_ENABLED="$(config_value HTTPS_ENABLED)"
  HTTPS_ENABLED="${HTTPS_ENABLED:-$([[ "$scheme" == https ]] && printf true || printf false)}"
  export FRONTEND_BASE_URL NGINX_PORT NGINX_HTTPS_PORT OPENCITADEL_DOMAIN HTTPS_ENABLED
}

configure_docker_socket_group() {
  local configured endpoint
  configured="$(config_value DOCKER_SOCK_GID)"
  if [[ -n "$configured" ]]; then DOCKER_SOCK_GID="$configured"; export DOCKER_SOCK_GID; return; fi
  [[ "$(uname -s)" == Linux ]] || return 0
  endpoint="${DOCKER_HOST:-}"
  if [[ -z "$endpoint" ]]; then
    endpoint="$(docker context inspect --format '{{(index .Endpoints "docker").Host}}' 2>/dev/null)" || return 0
  fi
  [[ "$endpoint" == unix:///var/run/docker.sock && -S /var/run/docker.sock ]] || return 0
  DOCKER_SOCK_GID="$(stat -c '%g' /var/run/docker.sock)" || return 1
  export DOCKER_SOCK_GID
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  CONFIG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  CONFIG_DEMO_MODE=0
  for argument in "$@"; do
    case "$argument" in
      --demo) CONFIG_DEMO_MODE=1 ;;
      *) printf 'ERROR: Unknown option: %s\n' "$argument" >&2; exit 1 ;;
    esac
  done
  configure_local_env "$CONFIG_ROOT" "$CONFIG_DEMO_MODE"
  printf 'Configuration is ready: %s\n' "$CONFIG_FILE"
  printf '%s\n' 'Run make quickstart to start the stack; credentials remain in the selected file.'
fi
