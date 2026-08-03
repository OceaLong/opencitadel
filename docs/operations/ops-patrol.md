[简体中文](ops-patrol.zh-CN.md)

# Ops Patrol operations

This runbook covers production enablement, Collector deployment, least privilege, validation, evidence, retention, recovery, and troubleshooting. For the domain contract, see [Ops Patrol architecture](../architecture/ops-patrol.md).

## Production readiness checklist

- API, Worker, PostgreSQL, Redis, migrations, and a tool-capable model are healthy.
- `AUDIT_SIGNING_KEY` is unique, secret-managed, and its key id/previous-key map follow the normal audit rotation procedure.
- The Collector has a dedicated read-only Kubernetes ServiceAccount and is not publicly reachable.
- Every namespace, workload, Prometheus query, HTTP/TLS/backup endpoint, and dependency is explicitly reviewed and registered.
- Collector egress NetworkPolicy matches those targets; registered endpoints are internal and return minimum status data without embedding credentials in URLs.
- All nine MCP tool policies are `integration_read` + `read_only` + `safe` + `never` approval.
- Fixture replay remains disabled in production.
- A dry run and one manual Run pass before scheduling is enabled.

## Security boundary

The Collector exports exactly nine fixed tools: capability discovery plus Kubernetes workload/events/logs and registered Prometheus, HTTP, TLS, backup, and dependency probes. It has no Shell, browser, write API, raw URL, or raw PromQL input. Collected strings are untrusted data. The API recomputes final status and evidence completeness.

Kubernetes RBAC is limited to `get`, `list`, and `watch`; Secrets, exec, attach, impersonation, and mutation verbs are excluded. The container runs as UID/GID 10001 with a read-only root filesystem, all Linux capabilities dropped, `RuntimeDefault` seccomp, and a writable 32 MiB `/tmp` only.

## Deployment

### Docker Compose

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
docker compose ps opencitadel-ops-collector
```

This profile verifies the MCP transport/configuration and can run registered non-Kubernetes probes. It does not mount the host kubeconfig. Use Helm or Kustomize with a dedicated ServiceAccount for real Kubernetes checks; do not solve local access by mounting privileged host credentials.

### Helm

Build/pull the `opencitadel-ops-collector` image, then use a protected values file:

```bash
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values production-values.yaml \
  --set opsCollector.enabled=true \
  --set opsCollector.image.repository=your-registry/opencitadel-ops-collector \
  --set opsCollector.image.tag=YOUR_RELEASE_TAG \
  --set opsCollector.targetRef=cluster-a \
  --set-json 'opsCollector.allowedNamespaces=["opencitadel"]'
```

Configure `allowedWorkloads` and every `registered*` map in the values file; the complete schema and example are in the [Collector README](../../ops-collector/README.md#configuration-reference). The UI wizard enables all built-in checks and therefore expects these ids (only a full custom API config can disable selected checks):

- `pvc-utilization`, `app-5xx-ratio`
- `primary-endpoint`, `primary-tls`
- `primary-database`, `primary-dependencies`

If `opsCollector.serviceAccount.create=false`, set `opsCollector.serviceAccount.name` to a pre-created account with equivalent least privilege. Do not grant Secret read or mutation verbs.

### Kustomize

```bash
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
kubectl apply -k deploy/kustomize/ops-collector
```

Treat the checked-in Kustomization as a base. Before applying it outside a disposable environment, patch the image/tag, target ref, allowlists, registered-target JSON environment values, resource limits, namespace, and NetworkPolicy egress.

### Network placement

Keep the Collector Service internal (`ClusterIP`). Ingress should allow only API/Worker on TCP 8090. Egress should allow DNS, the Kubernetes API, and exact registered target ports. The registry remains the authoritative SSRF boundary because port-only NetworkPolicy cannot validate hostnames or URL paths.

## Register the MCP Server

The Helm service URL is normally:

```text
http://opencitadel-ops-collector:8090/mcp
```

In **Settings → Integrations**, create the streamable-HTTP connection and enable it. The Developer Preview form does not author Tool Policies. As an authenticated administrator, persist them through `POST /api/app-config/mcp-servers/ops-collector/update` (or an equivalent approved bootstrap) with this complete request body:

```json
{
  "mcpServers": {
    "ops-collector": {
      "transport": "streamable_http",
      "enabled": true,
      "url": "http://opencitadel-ops-collector:8090/mcp",
      "tool_policies": {
        "get_capabilities": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "k8s_workload_summary": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "k8s_recent_events": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "k8s_pod_logs": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "prom_query": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "http_probe": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "certificate_status": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "backup_status": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"},
        "dependency_status": {"capability":"integration_read","effect":"read_only","idempotency":"safe","approval":"never","concurrency_group":"ops-patrol"}
      }
    }
  }
}
```

Normal UI connection edits omit `tool_policies`, so the service preserves the persisted map. Reapply/review the map whenever the Collector tool catalog changes.

Pack validation fails closed if a required policy is missing/conservative, the Server is disabled, live capability discovery fails, a schema hash differs, a required tool is absent, or the read-only dry run fails.

## Enablement order

1. Deploy migrations and confirm API/Worker health.
2. Deploy and restrict the Collector; verify its readiness and ServiceAccount.
3. Register and enable the MCP Server, then persist all fixed read-only Tool Policies through the authenticated management API.
4. As an administrator, open **Settings → Runtime → feature_flags** and set `enable_ops_patrol=true`. With DB AppConfig enabled, editing only `api/config.yaml` does not update an existing database row.
5. Keep `enable_ops_patrol_fixture_replay=false`.
6. Create a Pack and inspect its live preflight summary. The wizard auto-activates a successful validation; failed validation remains non-active until fixed, revalidated, and explicitly activated.
7. Trigger one manual Run and verify results/evidence before enabling its schedule.

The Pack schedule uses a five-field daily cron and an IANA timezone. Configuration changes increment the Pack version, pause its ScheduledJob, and require validation/activation again.

## Required configuration

| Setting | Ownership and limit |
|---------|---------------------|
| `feature_flags.enable_ops_patrol` | Global DB-backed AppConfig kill switch |
| `feature_flags.enable_ops_patrol_fixture_replay` | Test/demo only; false in production |
| `patrol_retention.run_days` / `finding_days` | Default 30 days, clamped to 1–90 |
| `patrol_retention.collector_evidence_days` | Default 7 days, clamped to 1–90 |
| `patrol_retention.cleanup_batch_size` | Default 100, clamped to 1–1000 per tick |
| `AUDIT_SIGNING_KEY` | HMAC key; rotate with audit key id and previous-key map |
| `OPS_COLLECTOR_*` | Collector-only target, allowlists, registries, concurrency, and output caps |

Collector variables and its Kubernetes identity belong to its deployment, not Pack tool arguments. Do not add raw destinations to a Pack.

## Verification

```bash
make test-patrol
helm lint deploy/helm/opencitadel
helm template opencitadel deploy/helm/opencitadel \
  --set opsCollector.enabled=true >/dev/null
kubectl kustomize deploy/kustomize/ops-collector >/dev/null
docker compose --env-file .env.example config --quiet
```

After deployment:

```bash
kubectl -n opencitadel rollout status deployment/opencitadel-ops-collector
kubectl auth can-i get pods \
  --as system:serviceaccount:opencitadel:opencitadel-ops-collector
kubectl auth can-i create pods \
  --as system:serviceaccount:opencitadel:opencitadel-ops-collector
kubectl auth can-i get secrets \
  --as system:serviceaccount:opencitadel:opencitadel-ops-collector
```

The first command should succeed, while both permission checks for `create pods` and `get secrets` must print `no`.

The destructive fixture suite must be run only with `./scripts/run-patrol-fixtures.sh`. It creates its own `kind-opencitadel-patrol-*` cluster, verifies reset baselines and write denial, scores all 20 cases, then deletes the cluster unless `PATROL_KEEP_DEMO_CLUSTER=true` is explicitly set.

## Evidence verification

Downloading a Run writes the audit action `patrol_evidence_downloaded`. The ZIP includes the Session audit material plus:

- `patrol/run.json`, `patrol/pack-snapshot.json`
- `patrol/check-results.json`, `patrol/findings.json`
- `patrol/report.md`, `patrol/evidence-index.json`
- `manifest.json`, `chain-signature.txt`

Verify every `manifest.json.file_hashes` entry before trusting the package. In a protected operator shell, compute `HMAC-SHA256(AUDIT_SIGNING_KEY, exact manifest.json bytes)` and compare it with the value after `manifest HMAC-SHA256:` in `chain-signature.txt`. Use the manifest `signing_key_id` to select the current or retained previous key. Never send the signing key to a browser or general-purpose verification service.

## Retention, backup, and restore

The Worker scheduler clears expired evidence references and deletes expired Findings/Runs in bounded leased ticks. Audit-chain rows are preserved. If the Scheduler is disabled, retention does not run; monitor both scheduler leadership and cleanup logs.

Back up PostgreSQL and object storage using the main [production deployment runbook](deployment.md). Patrol tables are part of the database backup. Restore the database, object storage, audit current/previous signing keys, and MCP/Collector configuration from the same recovery point, then validate Packs before resuming schedules.

Do not drop Patrol tables during rollback. To stop the feature safely, disable the feature flag; history remains available to authorized readers.

## Recovery and rollback

1. Disable `feature_flags.enable_ops_patrol` to stop new work without deleting data.
2. If only one target is unhealthy, pause the affected Pack.
3. Restore Collector connectivity or its registered target entry; never broaden to raw destinations.
4. Revalidate every changed Pack; activation is version-bound.
5. Re-enable the flag, run one manual Run, inspect evidence, then restore schedules.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Navigation missing / API says disabled | Global DB AppConfig `feature_flags.enable_ops_patrol`; config seed alone does not overwrite an existing row |
| No Collector in wizard | MCP Server is enabled, accessible in the current workspace scope, and uses streamable HTTP |
| `COLLECTOR_UNAVAILABLE` | MCP URL, Service/NetworkPolicy, readiness, DNS, last successful preflight |
| `COLLECTOR_CAPABILITY_MISMATCH` | Collector image/tool schema changed; pause and revalidate the Pack |
| `TARGET_SCOPE_DENIED` | Target ref, Namespace/Workload/registered-target allowlist |
| `AUTH_FAILED` | P0 registered HTTP probes do not accept arbitrary auth headers; use an approved internal status endpoint or disable the check, never embed credentials in its URL |
| `RATE_LIMITED` | Collector retries once; lower schedule concurrency or upstream load |
| `EVIDENCE_INCOMPLETE` | Required evidence type, canonical SHA-256, expiry, output truncation |
| Pack cannot activate | Current Pack version was not successfully validated, or capability/tool-policy check failed |
| Run remains queued/running | Worker/Redis health, model availability, active-run lock, Pack timeout (default 15 min, max 30 min) |
| Scheduled Runs absent | Pack active, schedule enabled, Scheduler enabled/leader, timezone/next run, feature flag still on |
| Retention not progressing | Worker scheduler loop, leader lease, `patrol_retention` limits, cleanup logs |

Logs should include Run, Pack, Session, check, request, target identifiers and a status/error code, but never credentials or raw authorization headers.
