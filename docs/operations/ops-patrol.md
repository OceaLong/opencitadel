[简体中文](ops-patrol.zh-CN.md)

# Ops Patrol operations

Ops Patrol runs entirely through the universal execution kernel. Collection is
read-only and deterministic; remediation is a separate, formally approved Run.
There is no Patrol-specific task queue or model-controlled write path.

## Production readiness

- API, execution kernel, PostgreSQL, Redis, migration, object storage, and the
  configured model endpoint are healthy.
- `AUDIT_SIGNING_KEY` is unique and managed as a secret.
- Collector and Actuator use distinct ServiceAccounts and NetworkPolicies.
- Every namespace, workload, query, and probe destination is registered.
- Fixture replay is disabled.
- A validated Pack and one manual Patrol Run succeed before scheduling.

## Security boundary

The Collector exports nine fixed tools: capability discovery, Kubernetes
workload/events/logs, and registered Prometheus, HTTP, TLS, backup, and
dependency probes. It has no shell, browser, mutation API, raw URL, or raw
PromQL input. Treat all returned strings as untrusted. The server validates the
closed-world schema and computes assertions itself.

The optional Actuator exposes only registered restart, scale, and rollback
operations for allowlisted Deployments/StatefulSets. It is never exposed to a
model. A `remediation` Run must persist approval before its
`remediation.execute` Activity can call the Actuator.

Both containers run non-root with a read-only root filesystem, dropped Linux
capabilities, `RuntimeDefault` seccomp, bounded `/tmp`, and internal-only
Services.

## Deploy Collector and Actuator

Compose can validate transport and non-Kubernetes probes:

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
docker compose --profile actuator up -d --build opencitadel-ops-actuator
```

Compose does not mount host Kubernetes credentials. Use Helm or Kustomize with
dedicated ServiceAccounts for real cluster checks.

```bash
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml \
  --set opsCollector.enabled=true \
  --set opsCollector.targetRef=cluster-a \
  --set-json 'opsCollector.allowedNamespaces=["opencitadel"]'

# Enable only when approved remediation is required.
helm upgrade opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --values values.production.yaml \
  --set opsActuator.enabled=true \
  --set opsActuator.targetRef=cluster-a \
  --set-json 'opsActuator.allowedNamespaces=["opencitadel"]' \
  --set-json 'opsActuator.allowedWorkloads={"opencitadel":{"opencitadel-api":{"kind":"deployment","min_replicas":2,"max_replicas":10}}}'
```

For Kustomize:

```bash
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
kubectl kustomize deploy/kustomize/ops-actuator >/dev/null
```

Patch image tags, target reference, allowlists, registered target maps,
namespace, resource limits, and exact egress before applying either base.
Collector ingress permits only API/execution-kernel on 8090; Actuator ingress
permits only API/execution-kernel on 8091. Collector egress is limited to DNS,
Kubernetes, and registered probe ports. Actuator egress is limited to DNS and
Kubernetes.

## Register the MCP Server

Register the Collector in **Settings → Integrations** as streamable HTTP:

```text
http://opencitadel-ops-collector:8090/mcp
```

Persist a conservative Tool Policy for every Collector tool:
`capability=integration_read`, `effect=read_only`, `idempotency=safe`, and
`approval=never`. Pack validation fails closed when a policy is absent, a
server is disabled, capability discovery fails, the schema hash drifts, or a
required tool is missing.

When remediation is enabled, register the Actuator under the exact name
`ops-actuator`:

```text
http://opencitadel-ops-actuator:8091/mcp
```

Actuator tools are invoked only by the remediation Activity; do not add them to
an Agent Skill or model tool catalog.

## Enable and validate

1. Confirm migration, API, and execution-kernel health.
2. Deploy and restrict the Collector.
3. Register it and persist all read-only Tool Policies.
4. Confirm `patrol_policy.admission=accepting` in Runtime Settings.
5. Keep production fixture replay disabled at deployment level.
6. Create, validate, and activate a Pack.
7. Run it manually and inspect results and evidence.
8. Enable its schedule only after the manual Run succeeds.
9. Optional: deploy/register the Actuator and set
   `patrol_policy.remediation=enabled`.
10. Propose, approve, execute, and verify one remediation end to end.

Pack changes create a new version and require validation/activation again.
Scheduled admission, Patrol execution, remediation approval, and verification
all produce formal Runs.

## Verification

```bash
make test-patrol
make test-actuator
helm lint deploy/helm/opencitadel
helm template opencitadel deploy/helm/opencitadel \
  --set opsCollector.enabled=true \
  --set opsActuator.enabled=true >/dev/null
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
kubectl kustomize deploy/kustomize/ops-actuator >/dev/null
docker compose --env-file .env.example config --quiet
```

After deployment, verify the Collector can read Pods but cannot create Pods or
read Secrets. Verify the Actuator can patch only explicitly registered
workloads and cannot read Secrets.

The destructive fixture suite must run only through
`./scripts/run-patrol-fixtures.sh`; it owns and removes its disposable kind
cluster unless explicitly configured to preserve it.

## Evidence verification

A downloaded Patrol evidence ZIP contains the frozen Pack snapshot, Run,
checks, findings, report, evidence index, manifest, and chain signature. Verify
every hash in `manifest.json`, then verify
`HMAC-SHA256(AUDIT_SIGNING_KEY, exact manifest bytes)` using the manifest's key
id. Never send the signing key to a browser or third-party verifier.

## Retention and recovery

The execution-kernel scheduler performs bounded, leased retention ticks.
Product Run/Finding references can expire according to `patrol_retention`;
audit-chain rows remain. Back up PostgreSQL, object storage, signing key
history, and integration configuration from the same recovery point.

To stop new Patrol work without destroying evidence, set
`patrol_policy.admission=paused` and pause affected Packs. Navigation and prior
Runs remain readable. Restore Collector connectivity or a registered target,
revalidate the Pack, run it manually, and only then set admission back to
`accepting` and restore scheduling. Never broaden access to a raw destination
as a recovery shortcut.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| New Runs rejected | global `patrol_policy.admission`, Pack activation |
| Collector unavailable | MCP URL, DNS, Service, NetworkPolicy, readiness |
| Capability mismatch | image/tool schema changed; revalidate Pack |
| Target denied | target ref and namespace/workload/endpoint allowlists |
| Evidence incomplete | required type, SHA-256, expiry, truncation |
| Run queued/running | execution-kernel health, PostgreSQL claims, model/Collector availability |
| Scheduled Runs absent | Pack activation, schedule, timezone, scheduler leader |
| Retention stalled | execution-kernel scheduler, leader lease, retention limits |
| Remediation rejected | approval decision, frozen parameter hash, capability baseline |
| Actuator failure | 8091 policy, readiness, exact workload allowlist |

Logs must include Run, Pack, Session, check, request, target, and error-code
identifiers, but never credentials or raw authorization headers.
