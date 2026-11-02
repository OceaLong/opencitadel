#!/bin/sh
set -eu

CONF="/etc/nginx/conf.d/default.conf"
SERVER_NAME="${OPENCITADEL_DOMAIN:-_}"
export OPENCITADEL_DOMAIN="${SERVER_NAME}"

HTTPS_ENABLED="${HTTPS_ENABLED:-false}"

case "${HTTPS_ENABLED}" in
  true|TRUE|1|yes|YES)
    if [ "${SERVER_NAME}" = "_" ] || [ -z "${SERVER_NAME}" ]; then
      echo "ERROR: OPENCITADEL_DOMAIN is required when HTTPS_ENABLED=true" >&2
      exit 1
    fi
    HTTPS_REDIRECT_ORIGIN="${FRONTEND_BASE_URL:-}"
    if [ -z "${HTTPS_REDIRECT_ORIGIN}" ]; then
      HTTPS_REDIRECT_ORIGIN="https://${SERVER_NAME}"
      if [ "${NGINX_HTTPS_PORT:-443}" != 443 ]; then
        HTTPS_REDIRECT_ORIGIN="${HTTPS_REDIRECT_ORIGIN}:${NGINX_HTTPS_PORT}"
      fi
    fi
    if ! printf '%s\n' "${HTTPS_REDIRECT_ORIGIN}" | grep -Eq '^https://(\[[[:xdigit:]:]+\]|[A-Za-z0-9.-]+)(:[0-9]+)?/?$'; then
      echo "ERROR: HTTPS requires a valid HTTPS FRONTEND_BASE_URL origin" >&2
      exit 1
    fi
    HTTPS_REDIRECT_ORIGIN="${HTTPS_REDIRECT_ORIGIN%/}"
    export HTTPS_REDIRECT_ORIGIN
    envsubst '${OPENCITADEL_DOMAIN} ${HTTPS_REDIRECT_ORIGIN}' \
      < /etc/nginx/templates-src/default.https.conf.template \
      > "${CONF}"
    ;;
  *)
    envsubst '${OPENCITADEL_DOMAIN}' \
      < /etc/nginx/templates-src/default.http.conf.template \
      > "${CONF}"
    ;;
esac

echo "Generated nginx config (HTTPS_ENABLED=${HTTPS_ENABLED}, OPENCITADEL_DOMAIN=${SERVER_NAME})"
