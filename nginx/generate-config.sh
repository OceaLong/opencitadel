#!/bin/sh
set -eu

CONF="/etc/nginx/conf.d/default.conf"
SERVER_NAME="${MANUS_DOMAIN:-_}"
export MANUS_DOMAIN="${SERVER_NAME}"

HTTPS_ENABLED="${HTTPS_ENABLED:-false}"

case "${HTTPS_ENABLED}" in
  true|TRUE|1|yes|YES)
    if [ "${SERVER_NAME}" = "_" ] || [ -z "${SERVER_NAME}" ]; then
      echo "ERROR: MANUS_DOMAIN is required when HTTPS_ENABLED=true" >&2
      exit 1
    fi
    envsubst '${MANUS_DOMAIN}' \
      < /etc/nginx/templates/default.https.conf.template \
      > "${CONF}"
    ;;
  *)
    envsubst '${MANUS_DOMAIN}' \
      < /etc/nginx/templates/default.http.conf.template \
      > "${CONF}"
    ;;
esac

echo "Generated nginx config (HTTPS_ENABLED=${HTTPS_ENABLED}, MANUS_DOMAIN=${SERVER_NAME})"
