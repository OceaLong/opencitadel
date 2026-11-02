[简体中文](README.zh-CN.md)

# Ops Patrol disposable fault lab

This lab is deliberately restricted to a `kind-opencitadel-patrol-*` context and a namespace carrying `opencitadel.io/disposable-patrol-demo=true`. Both fixture scripts fail closed for empty, production-looking, or unknown contexts.

Run `./scripts/run-patrol-fixtures.sh` from the repository root. It creates a disposable kind cluster, applies and resets all 20 cases, verifies the live baseline signature after every reset, checks the Collector ServiceAccount for zero write permission, observes the ten Kubernetes/log cases through the real Collector adapter, and runs the server-authoritative 20-case replay. The measured result is written to `tmp/patrol-fixture-score.json`; no score field is a hard-coded pass. Set `PATROL_KEEP_DEMO_CLUSTER=true` only for local debugging.

The setup manifests may create failing workloads and synthetic Warning events. Never apply them to a shared or production cluster.

CI also sets `PATROL_RUN_REMEDIATION_FIXTURE=true` to run case 21: observe the failure, restart through the authenticated actuator, verify idempotent replay, restore the healthy workload, and recheck recovery. The runner generates a temporary actuator token and passes it to the MCP client. Fixture RBAC grants the actual actuator ServiceAccount access in the demo namespace; its base deployment permissions in `opencitadel` remain in place.

Every manifest passes strict server-side schema validation before replay. On failure, the runner exports kind logs to `tmp/patrol-fixture-logs/` before deleting its cluster; CI uploads those logs and the actuator build log for diagnosis.

Prerequisites: Docker, kind, kubectl, jq, uv, and enough local capacity for the pinned kind node plus fixture images. The script preloads its runtime images, writes the machine-readable score under `tmp/`, and removes the cluster on success or failure unless the explicit keep flag is set.

This lab verifies Collector/Actuator adapters, fixed-fixture judgments, and permission boundaries. Patrol fixture 21 is remediation and uses a different numbering scheme from full-stack AC21 capacity; it does not establish the full product approval chain or capacity acceptance. Full AC21 capacity acceptance remains incomplete. The default runner creates/removes its own cluster; an explicit `PATROL_DEMO_CONTEXT` uses an existing disposable context without deleting that externally created cluster, and requires its fixture images and Python dependencies to be ready.

See [Ops Patrol operations](../../docs/operations/ops-patrol.md#verification) for release-gate expectations.
