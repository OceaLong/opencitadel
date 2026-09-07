#!/usr/bin/env bash
set -euo pipefail

context=${PATROL_DEMO_CONTEXT:-}
if [[ -z "$context" || "$context" != kind-opencitadel-patrol-* ]]; then
  echo "refusing non-disposable context" >&2
  exit 64
fi
# Check both the demo probe identity and the identity used by the deployed worker.
# The worker also retains the production RoleBinding in opencitadel; this gate
# deliberately checks isolation from unrelated default/kube-system namespaces.
for as_user in \
  system:serviceaccount:opencitadel-patrol-demo:patrol-actuator \
  system:serviceaccount:opencitadel:opencitadel-ops-actuator; do

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

  # Neither identity may act on unrelated namespaces, even if an accidental
  # ClusterRoleBinding would still let all demo-local assertions pass.
  for namespace in default kube-system; do
    for resource in deployments statefulsets secrets; do
      answer=$(kubectl --context "$context" auth can-i patch "$resource" --as="$as_user" -n "$namespace" || true)
      [[ "$answer" == "no" ]] || { echo "unexpected permission: $as_user patch $resource in $namespace" >&2; exit 1; }
    done
    for verb in get list watch; do
      answer=$(kubectl --context "$context" auth can-i "$verb" secrets --as="$as_user" -n "$namespace" || true)
      [[ "$answer" == "no" ]] || { echo "unexpected permission: $as_user $verb secrets in $namespace" >&2; exit 1; }
    done
  done
done

echo "both actuator identities passed demo permissions and unrelated namespace isolation checks"
