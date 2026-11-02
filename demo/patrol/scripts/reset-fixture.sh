#!/usr/bin/env bash
set -euo pipefail

context=${PATROL_DEMO_CONTEXT:-}
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
if [[ -z "$context" || "$context" != kind-opencitadel-patrol-* || "$context" =~ [Pp][Rr][Oo][Dd] ]]; then
  echo "refusing non-disposable Kubernetes context: ${context:-<empty>}" >&2
  exit 64
fi
kubectl --context "$context" delete namespace opencitadel-patrol-demo --ignore-not-found --wait=true
kubectl --context "$context" apply -f "$root_dir/demo/patrol/manifests/namespace.yaml"
kubectl --context "$context" apply -f "$root_dir/demo/patrol/manifests/collector-rbac.yaml"
kubectl --context "$context" apply -f "$root_dir/demo/patrol/manifests/actuator-rbac.yaml"
kubectl --context "$context" apply -f "$root_dir/demo/patrol/manifests/mock-services.yaml"
kubectl --context "$context" apply -f "$root_dir/demo/patrol/manifests/prometheus.yaml"
kubectl --context "$context" apply -f "$root_dir/demo/patrol/manifests/healthy-workload.yaml"
kubectl --context "$context" -n opencitadel-patrol-demo rollout status deployment/healthy-app --timeout=120s
kubectl --context "$context" -n opencitadel-patrol-demo rollout status deployment/patrol-mock --timeout=120s
