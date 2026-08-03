[简体中文](README.zh-CN.md)

# Ops Patrol disposable fault lab

This lab is deliberately restricted to a `kind-opencitadel-patrol-*` context and a namespace carrying `opencitadel.io/disposable-patrol-demo=true`. Both fixture scripts fail closed for empty, production-looking, or unknown contexts.

Run `./scripts/run-patrol-fixtures.sh` from the repository root. It creates a disposable kind cluster, applies and resets all 20 cases, verifies the live baseline signature after every reset, checks the Collector ServiceAccount for zero write permission, observes the ten Kubernetes/log cases through the real Collector adapter, and runs the server-authoritative 20-case replay. The measured result is written to `tmp/patrol-fixture-score.json`; no score field is a hard-coded pass. Set `PATROL_KEEP_DEMO_CLUSTER=true` only for local debugging.

The setup manifests may create failing workloads and synthetic Warning events. Never apply them to a shared or production cluster.

Prerequisites: Docker, kind, kubectl, jq, uv, and enough local capacity for the pinned kind node plus fixture images. The script preloads its runtime images, writes the machine-readable score under `tmp/`, and removes the cluster on success or failure unless the explicit keep flag is set.

See [Ops Patrol operations](../../docs/operations/ops-patrol.md#verification) for release-gate expectations.
