[English](README.md) · [简体中文](README.zh-CN.md)

# Deterministic Full-Stack Acceptance

OpenCitadel's release-blocking end-to-end suite is self-contained. It drives
the real public API and UI through PostgreSQL command admission, the execution
kernel, provider adapters, projections, approvals, SSE, dynamic Sandboxes,
Ops Collector, and shutdown cleanup. It does not require external model keys,
pre-provisioned product data, or an application-side test mode.

## Canonical command

From the repository root:

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

The runner allocates a unique Compose project, loopback ports, product resource
namespace, and Sandbox prefix. It builds the seven production images plus the
acceptance-only inference provider, starts the required profiles, runs every
dependency-ordered Playwright project, writes evidence, and drains only resources
with its exact ownership identity.

Use a partial project only while debugging; partial runs are marked as such in
the manifest and do not satisfy the release gate:

```bash
./scripts/run-acceptance-e2e.sh --playwright-project patrol-admin --disposable
```

## Product coverage

| Project | Product boundary |
| --- | --- |
| `identity` | Login/logout, teams, invitations, workspace isolation, anonymous denial |
| `control-plane` | Inference endpoint/model/probe/bindings/capabilities and Runtime Policy CAS/history/restore |
| `resources` | Knowledge-base build, publication, pinning, and fail-closed degradation |
| `execution` | Agent/Ask, SSE, approvals, rejection, cancellation, and Sandbox drain |
| `patrol-admin` | Formal Patrol validation/execution/evidence/admission, administration, compliance, mobile and keyboard access |

`contracts/acceptance-evidence.schema.json` is the source of truth for required
acceptance IDs. The zero-skip reporter fails on a missing, duplicated, skipped,
interrupted, or failed required ID.

## Deterministic inference boundary

`fixtures/inference-provider/` implements the narrow OpenAI-compatible protocol
used by production adapters. Its responses are deterministic functions of the
request. The provider is reachable only on the internal Compose network, runs
non-root with a read-only filesystem and dropped capabilities, and receives no
database, storage, OAuth, Docker, or production-provider credential.

The service exists only in the Compose `acceptance` profile. It is not part of
Helm, Kustomize, quickstart, production settings, or the release image matrix.
External-provider checks, if run separately, are compatibility canaries and do
not contribute required acceptance coverage.

## Evidence and cleanup

Each invocation writes `tmp/acceptance/<run-id>/manifest.json` plus logs, JUnit,
Playwright JSON, traces/screenshots on failure, image digests, migration head,
service health/restarts, Sandbox lifecycle, and residue counts. The manifest is
validated against `contracts/acceptance-evidence.schema.json` before success.

Ownership requires all applicable labels to agree:

- `com.docker.compose.project=<project-id>`;
- `com.opencitadel.acceptance.project=<project-id>`;
- `com.opencitadel.acceptance.run=<run-id>`;
- dynamic Sandboxes also carry `opencitadel.io/sandbox=true` and the run-scoped
  name prefix.

Without `--disposable`, product history and project volumes are retained for
local investigation and are reported in the manifest; containers, networks,
and dynamic Sandboxes are still drained. With `--disposable`, volumes created
by that invocation must also reach zero. The runner never uses a broad Docker
cleanup and must not touch unrelated projects or `voc-*` resources.

On failure, inspect `failure_reason`, `logs/stack.log`, and Playwright artifacts
under the run directory. Cleanup and evidence capture still execute. For runner
fault-path development, see the guarded options in `scripts/acceptance/runner.py`.

## Direct Playwright use

`npm test` is useful only when debugging against an already prepared acceptance
stack. It is not the release gate because it does not own stack isolation,
bootstrap, evidence validation, or cleanup.

```bash
cd e2e
npm ci
npx playwright install chromium
npm run test:meta
```

## Related documentation

- [Repository scripts](../scripts/README.md)
- [Production deployment](../docs/operations/deployment.md)
- [Ops Patrol operations](../docs/operations/ops-patrol.md)
