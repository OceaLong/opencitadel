#!/usr/bin/env bash
set -euo pipefail

context=${PATROL_DEMO_CONTEXT:-}
if [[ -z "$context" || "$context" != kind-opencitadel-patrol-* ]]; then
  echo "refusing non-disposable context" >&2
  exit 64
fi
as_user="system:serviceaccount:opencitadel-patrol-demo:patrol-actuator"

# The registered write baseline: only "patch" on the two workload kinds the
# actuator is allowed to remediate.
for resource in deployments statefulsets; do
  answer=$(kubectl --context "$context" auth can-i patch "$resource" --as="$as_user" -n opencitadel-patrol-demo || true)
  [[ "$answer" == "yes" ]] || { echo "expected permission missing: patch $resource" >&2; exit 1; }
done

# No other verb, on any resource, is granted -- create/delete/deletecollection
# must all be "no" for every resource the actuator ever touches.
for verb in create update delete deletecollection; do
  for resource in pods deployments statefulsets replicasets jobs secrets; do
    answer=$(kubectl --context "$context" auth can-i "$verb" "$resource" --as="$as_user" -n opencitadel-patrol-demo || true)
    [[ "$answer" == "no" ]] || { echo "unexpected permission: $verb $resource" >&2; exit 1; }
  done
done

# Secrets are entirely out of scope, including reads.
for verb in get list watch; do
  answer=$(kubectl --context "$context" auth can-i "$verb" secrets --as="$as_user" -n opencitadel-patrol-demo || true)
  [[ "$answer" == "no" ]] || { echo "unexpected permission: $verb secrets" >&2; exit 1; }
done

# No pod exec/attach, ever.
for subresource in pods/exec pods/attach; do
  answer=$(kubectl --context "$context" auth can-i create "$subresource" --as="$as_user" -n opencitadel-patrol-demo || true)
  [[ "$answer" == "no" ]] || { echo "unexpected permission: create $subresource" >&2; exit 1; }
done

echo "actuator service account is scoped to patch deployments/statefulsets only"
