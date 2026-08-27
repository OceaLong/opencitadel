#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command_name in docker kind kubectl jq uv; do
  command -v "$command_name" >/dev/null || { echo "$command_name is required" >&2; exit 69; }
done

cluster_name=${PATROL_DEMO_CLUSTER_NAME:-opencitadel-patrol-$(date +%s)}
if [[ "$cluster_name" != opencitadel-patrol-* || "$cluster_name" =~ [Pp][Rr][Oo][Dd] ]]; then
  echo "unsafe demo cluster name: $cluster_name" >&2
  exit 64
fi
created=false
if [[ -z "${PATROL_DEMO_CONTEXT:-}" ]]; then
  kind create cluster \
    --name "$cluster_name" \
    --image "${PATROL_KIND_NODE_IMAGE:-kindest/node:v1.35.0}" \
    --config "$repo_dir/deploy/patrol-demo/kind-config.yaml"
  export PATROL_DEMO_CONTEXT="kind-$cluster_name"
  created=true
  for image in nginx:1.27-alpine busybox:1.36 python:3.12-alpine; do
    docker pull "$image"
    kind load docker-image --name "$cluster_name" "$image"
  done
fi
if [[ "$PATROL_DEMO_CONTEXT" != kind-opencitadel-patrol-* ]]; then
  echo "refusing non-disposable context: $PATROL_DEMO_CONTEXT" >&2
  exit 64
fi
cleanup() {
  if [[ "$created" == true && "${PATROL_KEEP_DEMO_CLUSTER:-false}" != true ]]; then
    kind delete cluster --name "$cluster_name"
  fi
}
trap cleanup EXIT

"$repo_dir/deploy/patrol-demo/scripts/reset-fixture.sh"
"$repo_dir/deploy/patrol-demo/scripts/assert-read-only.sh"
"$repo_dir/deploy/patrol-demo/scripts/assert-actuator-write-scope.sh"

# Fixture 21 exercises the ops-actuator remediation flow (fail -> restart ->
# idempotent replay -> heal -> recheck pass) and is not part of the 20-case
# read-only replay this script otherwise drives. Off by default; opt in to
# build+load a real ops-actuator image and drive it through
# scripts/drive_remediation_fixture.py.
run_remediation_fixture=${PATROL_RUN_REMEDIATION_FIXTURE:-false}

deploy_ops_actuator() {
  # Built and loaded here (not via the docker-build CI job) so this script
  # stays runnable standalone, same as the nginx/busybox/python images above.
  mkdir -p "$repo_dir/tmp"
  echo "building opencitadel-ops-actuator:latest (log: tmp/ops-actuator-build.log)" >&2
  if ! docker build -t opencitadel-ops-actuator:latest "$repo_dir/ops-actuator" \
      > "$repo_dir/tmp/ops-actuator-build.log" 2>&1; then
    echo "opencitadel-ops-actuator image build failed; see tmp/ops-actuator-build.log" >&2
    tail -n 100 "$repo_dir/tmp/ops-actuator-build.log" >&2
    exit 1
  fi
  kind load docker-image --name "$cluster_name" opencitadel-ops-actuator:latest

  # Deployed into its own `opencitadel` namespace -- kept out of
  # opencitadel-patrol-demo on purpose so baseline_signature() (scoped to
  # opencitadel-patrol-demo only, see below) never observes it, and so the
  # actuator survives every per-fixture reset-fixture.sh namespace recreate.
  kubectl --context "$PATROL_DEMO_CONTEXT" create namespace opencitadel --dry-run=client -o yaml \
    | kubectl --context "$PATROL_DEMO_CONTEXT" apply -f -
  kubectl --context "$PATROL_DEMO_CONTEXT" apply -k "$repo_dir/deploy/kustomize/ops-actuator"

  # Kustomize's base env targets the production `opencitadel` namespace;
  # narrow it to the disposable fixture namespace and register the one
  # workload this demo is allowed to write, without a second overlay.
  kubectl --context "$PATROL_DEMO_CONTEXT" -n opencitadel set env deployment/opencitadel-ops-actuator \
    OPS_ACTUATOR_ALLOWED_NAMESPACES='["opencitadel-patrol-demo"]' \
    OPS_ACTUATOR_ALLOWED_WORKLOADS='{"opencitadel-patrol-demo":{"fixture-remediation-crashloop":{"kind":"deployment","min_replicas":0,"max_replicas":3}}}'
  kubectl --context "$PATROL_DEMO_CONTEXT" -n opencitadel rollout status deployment/opencitadel-ops-actuator --timeout=120s

  # No NetworkPolicy change needed: scripts/drive_remediation_fixture.py
  # reaches the actuator via `kubectl port-forward`, which tunnels through
  # the kubelet directly into the pod's network namespace rather than the
  # CNI's pod-to-pod path that NetworkPolicy governs, so the actuator's
  # production-shaped ingress policy (API/execution-kernel pods only) stays intact.
}

if [[ "$run_remediation_fixture" == true ]]; then
  deploy_ops_actuator
fi

start_seconds=$SECONDS
baseline_signature() {
  kubectl --context "$PATROL_DEMO_CONTEXT" -n opencitadel-patrol-demo \
    get deployments,services,configmaps,serviceaccounts,roles,rolebindings,cronjobs -o json \
    | jq -Sc '[.items[] | {
        apiVersion,
        kind,
        name: .metadata.name,
        data: (.data // null),
        rules: (.rules // null),
        roleRef: (.roleRef // null),
        subjects: (.subjects // null),
        spec: ((.spec // null) | if type == "object" then del(.clusterIP, .clusterIPs, .ipFamilies, .ipFamilyPolicy, .internalTrafficPolicy) else . end)
      }] | sort_by(.kind, .name)' \
    | cksum
}

baseline=$(baseline_signature)
verified_resets=0
verified_live_cases=0
for fixture in "$repo_dir"/deploy/patrol-demo/fixtures/*; do
  case_id=$(basename "$fixture")
  if [[ "$case_id" == 21-* && "$run_remediation_fixture" != true ]]; then
    continue
  fi
  "$repo_dir/deploy/patrol-demo/scripts/apply-fixture.sh" "$case_id"
  case "$case_id" in
    01-*|02-*|03-*|04-*|05-*|06-*|07-*|08-*|09-*|20-*)
      (
        cd "$repo_dir/ops-collector"
        PATROL_FIXTURE_CASE=$case_id \
          UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/opencitadel-uv-cache} \
          uv run --no-sync pytest -q \
            tests/integration/test_golden_fixtures.py::test_real_collector_observes_selected_live_fixture
      )
      verified_live_cases=$((verified_live_cases + 1))
      ;;
    21-*)
      # apply-fixture.sh already waited for the aggregate restartCount>=11
      # threshold above; drive_remediation_fixture.py owns every remaining
      # deterministic assertion (collector fail-state, restart_workload,
      # idempotent replay, heal, collector pass-state).
      (
        cd "$repo_dir/ops-collector"
        PATROL_DEMO_CONTEXT="$PATROL_DEMO_CONTEXT" \
          UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/opencitadel-uv-cache} \
          uv run --no-sync python "$repo_dir/scripts/drive_remediation_fixture.py"
      )
      verified_live_cases=$((verified_live_cases + 1))
      ;;
  esac
  "$repo_dir/deploy/patrol-demo/scripts/reset-fixture.sh"
  current=$(baseline_signature)
  if [[ "$current" != "$baseline" ]]; then
    echo "fixture $case_id did not restore the live baseline signature" >&2
    exit 1
  fi
  verified_resets=$((verified_resets + 1))
done

(
  cd "$repo_dir/ops-collector"
  UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/opencitadel-uv-cache} uv run --no-sync pytest -q tests/integration/test_golden_fixtures.py
)
(
  cd "$repo_dir/api"
  UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/opencitadel-uv-cache} uv run pytest -q tests/app/integration/test_patrol_*.py
)

duration=$((SECONDS - start_seconds))
(
  cd "$repo_dir/api"
  PATROL_BASELINE_RESETS_VERIFIED=$verified_resets \
  PATROL_LIVE_FIXTURE_CASES_VERIFIED=$verified_live_cases \
  PATROL_COLLECTOR_LIVE_BASELINE_VERIFIED=true \
  UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/opencitadel-uv-cache} \
    uv run python "$repo_dir/scripts/score_patrol_fixtures.py" \
      --output "$repo_dir/tmp/patrol-fixture-score.json" \
      --wall-seconds "$duration"
)
echo "Patrol fixture score: $repo_dir/tmp/patrol-fixture-score.json"
