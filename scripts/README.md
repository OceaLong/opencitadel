[English](README.md) · [简体中文](README.zh-CN.md)

# Repository Scripts

Operational and documentation maintenance scripts at the repository root.

## Scripts

| Script | Purpose |
|--------|---------|
| [`quickstart.sh`](quickstart.sh) | First-run onboarding: create `.env`, build `opencitadel-sandbox`, start Compose stack |
| [`check-docs.sh`](check-docs.sh) | CI documentation checks: bilingual pairs, index coverage, stale content guards |
| [`run-acceptance-e2e.sh`](run-acceptance-e2e.sh) | Own an isolated full-stack acceptance run, evidence manifest, and exact-label cleanup |
| [`run-patrol-fixtures.sh`](run-patrol-fixtures.sh) | Create a disposable kind cluster, run/reset 20 Patrol cases, verify Collector read-only access, and score the gate |
| [`score_patrol_fixtures.py`](score_patrol_fixtures.py) | Validate the machine-readable Patrol fixture result; normally invoked by the runner |

## Usage

```bash
# Recommended first run (also: make quickstart)
bash scripts/quickstart.sh

# Non-interactive (CI / no TTY)
QUICKSTART_NONINTERACTIVE=1 bash scripts/quickstart.sh

# Documentation consistency (run before docs PRs)
./scripts/check-docs.sh

# Release-blocking deterministic acceptance; removes only invocation-owned volumes
./scripts/run-acceptance-e2e.sh --disposable

# Destructive fixtures, but only inside the script-created disposable kind cluster
./scripts/run-patrol-fixtures.sh
```

Do not invoke individual Patrol fixture setup manifests against a shared context. The runner enforces a `kind-opencitadel-patrol-*` context and disposable namespace label, and removes the cluster unless `PATROL_KEEP_DEMO_CLUSTER=true` is explicitly set for debugging.

The acceptance runner is the only supported full-stack E2E entrypoint. It
allocates a unique Compose project and run id, validates loopback ports, emits
`tmp/acceptance/<run-id>/manifest.json`, and requires exact project/run labels
before inspecting or removing Docker resources. Omit `--disposable` to retain
only project volumes and product history for investigation; containers,
networks, and dynamic Sandboxes are always drained. Never substitute a broad
`docker system prune` or a fixed Compose project name.

## Related

- [Self-host in 10 minutes](../docs/tutorials/01-self-host-10-minutes.md)
- [Documentation maintenance checklist](../docs/MAINTENANCE_CHECKLIST.md)
- [Deterministic full-stack acceptance](../e2e/README.md)
- [Deploy scripts](../deploy/scripts/README.md) — production host tuning
- [Ops Patrol fault lab](../deploy/patrol-demo/README.md)
