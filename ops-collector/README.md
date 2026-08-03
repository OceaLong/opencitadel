[简体中文](README.zh-CN.md)

# OpenCitadel Ops Collector

The Ops Collector is a separately deployable, read-only MCP service for Ops Patrol. It exposes exactly nine fixed operations and accepts registered identifiers instead of raw PromQL, URLs, SQL, Kubernetes paths, or shell commands.

## Operations

| Tool | Upstream access | Bounded input |
|------|-----------------|---------------|
| `get_capabilities` | None | Returns tool/schema/capability hashes |
| `k8s_workload_summary` | Kubernetes read API | Allowlisted namespace and workloads |
| `k8s_recent_events` | Kubernetes read API | Allowlisted namespace, time window, limit |
| `k8s_pod_logs` | Kubernetes pod logs | Allowlisted namespace, bounded tail/window |
| `prom_query` | Prometheus HTTP API | Registered query id only |
| `http_probe` | HTTP | Registered probe id only |
| `certificate_status` | TLS | Registered HTTPS probe id only |
| `backup_status` | Backup status endpoint | Registered backup id only; never reads backup data |
| `dependency_status` | TCP connectivity | Registered dependency id only |

Every operation is annotated read-only, non-destructive, idempotent, and closed-world. Each response uses the same envelope with `target_ref`, status, duration, bounded data, evidence references, warnings, and a stable error code.

## Configuration reference

Configuration is environment-only and uses the `OPS_COLLECTOR_` prefix. Structured values are JSON.

| Variable | Default / range | Purpose |
|----------|-----------------|---------|
| `TARGET_REF` | `opencitadel-local` | Stable identity matched by the Pack |
| `ALLOWED_NAMESPACES` | `["opencitadel"]` | Non-empty namespace allowlist |
| `ALLOWED_WORKLOADS` | `{}` | JSON map of namespace to allowed workload ids; an empty list allows all workloads in an allowed namespace |
| `PROMETHEUS_QUERIES` | `{}` | Map of query id to `base_url`, fixed `promql`, and optional timeout |
| `HTTP_PROBES` | `{}` | Map of probe id to `url`, expected status list, and optional timeout |
| `CERTIFICATE_PROBES` | `{}` | Map of probe id to HTTPS `url` and optional timeout |
| `BACKUPS` | `{}` | Map of backup id to `status_url` and optional timeout |
| `DEPENDENCIES` | `{}` | Map of id to one target or a list of `{kind,host,port,timeout_seconds}` |
| `TRANSPORT` | `streamable-http` | `streamable-http` or development-only `stdio` |
| `ALLOW_STDIO` | `false` | Must also be true before stdio can start |
| `HOST` / `PORT` | `0.0.0.0` / `8090` | Listener for streamable HTTP (`/mcp`) |
| `CONCURRENCY` | `4`, range 1–8 | Maximum simultaneous probes |
| `MAX_OUTPUT_BYTES` | `65536`, max 1 MiB | Serialized response cap |
| `MAX_ROWS` | `200`, max 1000 | Tabular/sample cap |
| `MAX_ARRAY_ITEMS` | `200`, max 1000 | Per-array cap |
| `MAX_STRING_CHARS` | `32768`, max 131072 | Per-string cap |

The complete variable name is the prefix plus the table name, for example `OPS_COLLECTOR_HTTP_PROBES`.

Example Helm values use native YAML objects and render them to JSON:

```yaml
opsCollector:
  enabled: true
  targetRef: production-cluster-a
  allowedNamespaces: [opencitadel]
  allowedWorkloads:
    opencitadel: [opencitadel-api, opencitadel-worker]
  registeredPrometheusQueries:
    app-5xx-ratio:
      base_url: http://prometheus.monitoring.svc:9090
      promql: sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))
  registeredHttpProbes:
    primary-endpoint:
      url: https://opencitadel.example.com/api/status
      expected_statuses: [200]
  registeredCertificateProbes:
    primary-tls:
      url: https://opencitadel.example.com
  registeredBackups:
    primary-database:
      status_url: https://backup-status.example.internal/latest
  registeredDependencies:
    primary-dependencies:
      - {kind: postgres, host: postgres.database.svc, port: 5432}
      - {kind: redis, host: redis.cache.svc, port: 6379}
```

The built-in Kubernetes baseline references `pvc-utilization`, `app-5xx-ratio`, `primary-tls`, `primary-database`, `primary-dependencies`, and `primary-endpoint`. The UI wizard enables every baseline check, so register every id before validation. Custom API clients may instead submit a full Pack config with selected checks disabled.

## Run locally

Streamable HTTP (`/mcp`, port `8090`) is the default:

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
```

The Compose profile is useful for transport/configuration checks and non-Kubernetes registered probes. It does not mount a host kubeconfig. For real Kubernetes observations, deploy with the Helm or Kustomize ServiceAccount instead of adding a privileged host credential mount.

For development-only stdio:

```bash
OPS_COLLECTOR_ALLOW_STDIO=true uv run opencitadel-ops-collector --transport stdio
```

Never enable stdio in the production deployment.

## Kubernetes deployment

- Helm: set `opsCollector.enabled=true` and configure the registries in `deploy/helm/opencitadel/values.yaml`.
- Kustomize: use `deploy/kustomize/ops-collector` as a base and patch its image, target ref, allowlists, and registered-target environment values.
- Keep the Service `ClusterIP`; only API and Worker should reach port 8090.
- Keep the read-only ServiceAccount. It excludes Secrets, exec, attach, impersonation, and all mutation verbs.
- Review NetworkPolicy egress against the exact Kubernetes API and registered target locations. The registry is the application-layer SSRF boundary; NetworkPolicy is defense in depth.

The container runs as UID/GID 10001 with a read-only root filesystem, all Linux capabilities dropped, `RuntimeDefault` seccomp, and only a bounded writable `/tmp`.

## Authentication and data handling

Kubernetes access uses the Pod ServiceAccount; that credential never becomes a tool argument or response field. The Developer Preview does not accept arbitrary authorization headers for registered Prometheus/HTTP/backup probes. Use network-internal, least-data status endpoints that do not require application credentials, or disable the check. Never place credentials in a registered URL.

The Collector redacts authorization-shaped values, passwords, API keys, tokens, connection strings, cookies, JWT-shaped values, and secret-shaped object fields before output limiting. Do not rely on redaction as the only control: make status responses minimal and never expose the Collector publicly.

## Development and verification

```bash
uv sync --frozen
uv run pytest -q
```

The destructive golden-fixture lab is run only from the repository root with `./scripts/run-patrol-fixtures.sh`. See `deploy/patrol-demo/README.md`; never apply those fixtures to a shared cluster.

## Related documentation

- [Ops Patrol architecture](../docs/architecture/ops-patrol.md)
- [Ops Patrol operations](../docs/operations/ops-patrol.md)
- [Run a Patrol](../docs/tutorials/06-ops-patrol.md)
