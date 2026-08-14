#!/usr/bin/env bash
set -euo pipefail

command -v jq >/dev/null || { echo "jq is required" >&2; exit 69; }

case_id=${1:?usage: apply-fixture.sh <case-id>}
context=${PATROL_DEMO_CONTEXT:-}
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
fixture_dir="$root_dir/deploy/patrol-demo/fixtures/$case_id"

if [[ -z "$context" || "$context" != kind-opencitadel-patrol-* || "$context" =~ [Pp][Rr][Oo][Dd] ]]; then
  echo "refusing non-disposable Kubernetes context: ${context:-<empty>}" >&2
  exit 64
fi
if [[ ! -f "$fixture_dir/setup.yaml" ]]; then
  echo "unknown fixture: $case_id" >&2
  exit 66
fi
if [[ $(kubectl --context "$context" get namespace opencitadel-patrol-demo -o jsonpath='{.metadata.labels.opencitadel\.io/disposable-patrol-demo}') != "true" ]]; then
  echo "context does not contain the disposable patrol namespace label" >&2
  exit 65
fi

kubectl --context "$context" -n opencitadel-patrol-demo delete deployment,job,cronjob,pod,event,networkpolicy -l opencitadel.io/patrol-fixture=true --ignore-not-found
kubectl --context "$context" apply -f "$fixture_dir/setup.yaml"

if [[ "$case_id" == "03-crashloop" || "$case_id" == "06-restarts-warn" || "$case_id" == "07-restarts-fail" || "$case_id" == "21-remediation-crashloop" ]]; then
  target=4
  [[ "$case_id" == "03-crashloop" || "$case_id" == "07-restarts-fail" || "$case_id" == "21-remediation-crashloop" ]] && target=11
  deadline=$((SECONDS + 180))
  while (( SECONDS < deadline )); do
    count=$(kubectl --context "$context" -n opencitadel-patrol-demo get pod -l "app=fixture-${case_id#??-}" -o json 2>/dev/null \
      | jq '[.items[].status.containerStatuses[]?.restartCount] | add // 0' || true)
    [[ "${count:-0}" =~ ^[0-9]+$ ]] && (( count >= target )) && break
    sleep 2
  done
  [[ "${count:-0}" =~ ^[0-9]+$ ]] && (( count >= target )) || { echo "restart threshold $target not reached" >&2; exit 1; }
fi
