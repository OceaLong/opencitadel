[English](README.md) · [简体中文](README.zh-CN.md)

# Repository Scripts

Operational and documentation maintenance scripts at the repository root.

## Scripts

| Script                                                                                                                       | Purpose                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`backup.sh`](backup.sh) / [`backup_tool.py`](backup_tool.py)                                                                | Quiesced Compose database + object backup with integrity manifest                                                                                |
| [`verify-backup.sh`](verify-backup.sh) / [`restore.sh`](restore.sh)                                                          | Offline checks and a fresh-volume recovery drill; see the [runbook](../docs/operations/deployment.md#local-compose-backup-and-isolated-recovery) |
| [`configure-local.sh`](configure-local.sh) | Generate and persist first-install credentials without starting containers (`make configure`) |
| [`quickstart.sh`](quickstart.sh)                                                                                             | First-run onboarding: create `.env`, build `opencitadel-sandbox`, start Compose stack                                                            |
| [`check-docs.sh`](check-docs.sh)                                                                                             | CI documentation checks: bilingual pairs, index coverage, stale content guards                                                                   |
| [`run-acceptance-e2e.sh`](run-acceptance-e2e.sh)                                                                             | Own an isolated full-stack acceptance run, evidence manifest, and exact-label cleanup                                                            |
| [`run-patrol-fixtures.sh`](run-patrol-fixtures.sh)                                                                           | Create a disposable kind cluster, run/reset 20 Patrol cases, verify Collector read-only access, and score the gate                               |
| [`score_patrol_fixtures.py`](score_patrol_fixtures.py)                                                                       | Validate the machine-readable Patrol fixture result; normally invoked by the runner                                                              |
| [`drive_remediation_fixture.py`](drive_remediation_fixture.py)                                                               | Deterministic, LLM-free harness for the remediation fixture; invoked by `run-patrol-fixtures.sh` when `PATROL_RUN_REMEDIATION_FIXTURE=true`      |
| [`verify_evidence_package.py`](verify_evidence_package.py)                                                                   | Verify an evidence package ZIP offline (manifest HMAC signature + per-file SHA-256 digests)                                                      |
| [`seed_execution_visualization.py`](seed_execution_visualization.py) / [`execution_capacity/`](execution_capacity/README.md) | Owned historical capacity source construction, finite live load and reference evidence tooling; full AC21 runtime gate remains incomplete        |
| [`acceptance/`](acceptance/)                                                                                                 | Python helpers behind `run-acceptance-e2e.sh`: ownership-safe orchestration (`runner.py`) and evidence manifests (`manifest.py`)                 |

## Usage

`acceptance/` holds the Python orchestration behind the E2E runner;
`execution_capacity/` holds capacity source and proof tooling. Capacity tests
live in `execution_capacity/tests/` and run with `make test-capacity` from the
repository root, using the API dependency environment. This includes large
local proof fixtures and is separate from the default API test collection.
Passing these helper tests does not establish full AC21 acceptance.

Quickstart requires a running Docker daemon (`docker info` must succeed). It
checks this before creating configuration or building images. `make configure`
can still prepare configuration without Docker.

```bash
# Recommended first run (also: make quickstart)
bash scripts/quickstart.sh

# Prepare only; no Docker startup and no interactive input
make configure

# Documentation consistency (run before docs PRs)
./scripts/check-docs.sh

# Full acceptance attempt (AC21 prerequisites described in e2e/README.md); removes only owned volumes
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

For full or `execution` acceptance, AC21 additionally requires report/fixture
inputs and a trusted private `OfflineProofContext`. The CLI currently does not
construct that context, and full reference collection/reuse remains incomplete.
See the [acceptance status](../e2e/README.md#ac21-capacity-prerequisite-and-current-status)
before interpreting a product-test result as a passing release gate.

## Related

- [Self-host in 10 minutes](../docs/tutorials/01-self-host-10-minutes.md)
- [Documentation maintenance checklist](../docs/MAINTENANCE_CHECKLIST.md)
- [Deterministic full-stack acceptance](../e2e/README.md)
- [Deploy scripts](../deploy/scripts/README.md) — production host tuning
- [Ops Patrol fault lab](../demo/patrol/README.md)
