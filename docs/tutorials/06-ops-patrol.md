[简体中文](06-ops-patrol.zh-CN.md)

# Run a read-only daily Ops Patrol

Ops Patrol checks a self-hosted Kubernetes application through a fixed, read-only MCP Collector. The server—not the Agent—evaluates thresholds, creates Findings, and signs the downloadable evidence package.

## Before you start

You need:

- a running API, Worker, PostgreSQL, Redis, and tool-capable model;
- an Ops Collector deployed in the target cluster with its dedicated read-only ServiceAccount;
- reviewed namespace/workload allowlists and registered probes;
- an enabled streamable-HTTP MCP Server whose nine tool policies are fixed read-only;
- an administrator who can enable the global feature flag;
- Operator access to the target workspace. Auditors can review but cannot create, trigger, or decide Findings.

For a transport-only local check, you may start:

```bash
docker compose --profile patrol up -d --build opencitadel-ops-collector
```

The Compose profile does not mount a host kubeconfig. Use the Helm/Kustomize ServiceAccount for real Kubernetes observations. Never expose the Collector publicly.

## Prepare the Collector

The built-in `kubernetes-baseline-v1` wizard creates ten enabled checks. Before validation, register the external targets that use these identifiers:

| Check | Required registered id |
|-------|------------------------|
| PVC utilization | `pvc-utilization` Prometheus query |
| HTTP 5xx ratio | `app-5xx-ratio` Prometheus query |
| Endpoint health | `primary-endpoint` HTTP probe |
| Certificate expiry | `primary-tls` certificate probe |
| Backup freshness | `primary-database` backup status |
| Dependency health | `primary-dependencies` dependency group |

The four Kubernetes checks use the Pack namespace and the Collector namespace/workload allowlists. Full configuration examples are in the [Collector README](../../ops-collector/README.md#configuration-reference).

In **Settings → Integrations**, register the internal URL (for the default Helm release, `http://opencitadel-ops-collector:8090/mcp`) as streamable HTTP and enable it. Then use the authenticated management API payload in [Ops Patrol operations](../operations/ops-patrol.md#register-the-mcp-server) to persist all nine Tool Policies; the current UI form does not author those policies.

As an administrator, open **Settings → Runtime → feature_flags**, enable `enable_ops_patrol`, and keep `enable_ops_patrol_fixture_replay` disabled. If DB AppConfig is enabled, changing only `api/config.yaml` does not overwrite the existing global configuration row.

## Create and validate a Pack

1. Open **Ops Patrol → Create patrol**.
2. Select the Collector. The wizard never accepts a raw URL or PromQL expression.
3. Set a stable target reference matching `OPS_COLLECTOR_TARGET_REF`, plus cluster, namespace, and environment.
4. Review all ten fixed checks and thresholds. The Developer Preview wizard does not edit or disable individual checks; create a full custom Pack config through the API if that control is required.
5. Choose an IANA timezone and daily five-field cron schedule. Leave the schedule disabled for the first run.
6. Select **Create and dry run**.
7. Open the Pack and inspect validation errors, enabled tools, capability hash, and dry-run summary.
8. On success the wizard activates the Pack automatically. On failure it leaves the Pack non-active; fix the Collector/configuration, select **Revalidate**, inspect the new summary, and activate only when the current version passes.

Changing the Pack later increments its version, pauses scheduling, and requires validation/activation again.

## Run and review

1. Select **Run now**. The UI sends a unique `Idempotency-Key`; repeated submission does not create a duplicate Run.
2. Open the Run and wait for a terminal state.
3. Review each server-computed result, observed fields, assertion details, evidence references, and error codes.
4. Decide every actionable Finding with **Acknowledge**, **Resolved**, or **False positive**. A false-positive reason is mandatory.
5. Download the evidence ZIP and verify its SHA-256 manifest/HMAC according to the operations runbook.
6. After the manual Run is trustworthy, enable the Pack schedule and confirm `next_run_at` uses the intended timezone.

The Collector itself never gains mutation rights. A separate, narrowly-scoped Ops Actuator adds an approval-gated repair path on top of this Pack — see [Approve an Ops Patrol remediation](07-approved-remediation.md).

## Interpret results

| Status | Meaning |
|--------|---------|
| `pass` | All configured server assertions passed |
| `warn` / `fail` | Threshold breach; a Finding may be created or deduplicated |
| `error` | Probe, schema, capability, or required-evidence failure; not healthy |
| `skipped` | Explicitly skipped according to the Pack missing-data contract; not silently passed |

The Pack detail shows 30-day scheduled-run success, Finding and false-positive counts, and median review time. Review time remains absent until an operator opens a Run and decides a Finding.

## Safe rollback

Set `feature_flags.enable_ops_patrol=false` in global Runtime settings. Navigation and new work disappear, schedules stop creating Runs, and existing authorized Runs/evidence remain readable. Re-enabling the flag does not discard configuration or history.

See [Ops Patrol operations](../operations/ops-patrol.md) for deployment, permissions, evidence verification, backup/restore, recovery, and troubleshooting.

## Next

- [Approve an Ops Patrol remediation](07-approved-remediation.md)
