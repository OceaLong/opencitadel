#!/usr/bin/env bash
set -euo pipefail

context=${PATROL_DEMO_CONTEXT:-}
if [[ -z "$context" || "$context" != kind-opencitadel-patrol-* ]]; then
  echo "refusing non-disposable context" >&2
  exit 64
fi
as_user="system:serviceaccount:opencitadel-patrol-demo:patrol-collector"
for verb in create update patch delete deletecollection; do
  for resource in pods deployments jobs secrets; do
    answer=$(kubectl --context "$context" auth can-i "$verb" "$resource" --as="$as_user" -n opencitadel-patrol-demo || true)
    [[ "$answer" == "no" ]] || { echo "unexpected permission: $verb $resource" >&2; exit 1; }
  done
done
echo "collector service account has no tested write permission"
