[English](README.md) · [简体中文](README.zh-CN.md)

# Repository Scripts

Operational and documentation maintenance scripts at the repository root.

## Scripts

| Script | Purpose |
|--------|---------|
| [`quickstart.sh`](quickstart.sh) | First-run onboarding: create `.env`, build `opencitadel-sandbox`, start Compose stack |
| [`check-docs.sh`](check-docs.sh) | CI documentation checks: bilingual pairs, index coverage, stale content guards |
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

# Destructive fixtures, but only inside the script-created disposable kind cluster
./scripts/run-patrol-fixtures.sh
```

Do not invoke individual Patrol fixture setup manifests against a shared context. The runner enforces a `kind-opencitadel-patrol-*` context and disposable namespace label, and removes the cluster unless `PATROL_KEEP_DEMO_CLUSTER=true` is explicitly set for debugging.

## Related

- [Self-host in 10 minutes](../docs/tutorials/01-self-host-10-minutes.md)
- [Documentation maintenance checklist](../docs/MAINTENANCE_CHECKLIST.md)
- [Deploy scripts](../deploy/scripts/README.md) — production host tuning
- [Ops Patrol fault lab](../deploy/patrol-demo/README.md)
