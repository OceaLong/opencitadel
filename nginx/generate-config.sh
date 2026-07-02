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
    envsubst '${OPENCITADEL_DOMAIN}' \
      < /etc/nginx/templates/default.https.conf.template \
      > "${CONF}"
    ;;
  *)
    envsubst '${OPENCITADEL_DOMAIN}' \
      < /etc/nginx/templates/default.http.conf.template \
      > "${CONF}"
    ;;
esac

echo "Generated nginx config (HTTPS_ENABLED=${HTTPS_ENABLED}, OPENCITADEL_DOMAIN=${SERVER_NAME})"
