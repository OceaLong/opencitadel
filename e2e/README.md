[English](README.md) · [简体中文](README.zh-CN.md)

# Deterministic Full-Stack Acceptance

OpenCitadel's end-to-end product suite drives
the real public API and UI through PostgreSQL command admission, the execution
kernel, provider adapters, projections, approvals, SSE, dynamic Sandboxes,
Ops Collector, and shutdown cleanup. It does not require external model keys,
pre-provisioned product data, or an application-side test mode. The full release
gate additionally requires AC21 reference-capacity evidence; see its current
limitations below.

## Canonical command

From the repository root:

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

The runner allocates a unique Compose project, loopback ports, product resource
namespace, and Sandbox prefix. It builds the production services and Sandbox image plus the
acceptance-only inference provider, starts the required profiles, runs every
dependency-ordered Playwright project, writes evidence, and drains only resources
with its exact ownership identity.
Retained scratch scenarios under `e2e/tmp/` are excluded from canonical collection.

Execution and resource cases retire only sessions created by their exact browser
page, after all history, artifact, citation and comparison reads finish. Public
session soft deletion releases the associated Sandbox; successful retirement
completes its durable cleanup entry, while failures remain pending for final
cleanup. Bootstrap strict sources and shared patrol sessions stay available until
their consumers finish. This keeps production Sandbox quota and memory thresholds
unchanged. Retry evidence observers bind `ACCEPTANCE_PROJECT_ID` to this run's
lifecycle and exact container labels, supporting both default and explicit
Compose project names.

For runs that were not cancelled, full and `execution` runs validate the current native
strict report and restoration receipt, including when browser checks failed.
Only a valid producer and proved restoration allow the six assertions in
`api/tests/app/integration/test_execution_visualization_closed_loop.py` to run.
The runner passes this invocation's evidence directory and identity, retains
`strict-pytest.xml`, and requires six passes with zero skips. Browser, strict and
capacity failures remain in the manifest and keep the complete command nonzero;
passing consumers cannot erase an earlier failure.
CI's API job and `make test-api` / `make test-api-strict` exclude only this
consumer file; it requires the acceptance producer and cannot run against an
ordinary API-test database or historical evidence.

SIGINT/SIGTERM interrupts the runner's currently owned command process group,
including its CLI descendants. After a five-second cancellation grace period,
the runner sends SIGKILL to that same group if it remains active. It records an
interrupted failure and disables cancellation probing during cleanup so owned
cleanup commands can run. Sandbox event recording continues to capture lifecycle
evidence, and cleanup uses exact ownership labels. Cancellation never becomes a pass; missing
or unexecuted strict consumers remain unproved. This does not promise a fixed
deadline for an arbitrary external process that ignores interruption.

Use a partial project only while debugging; partial runs are marked as such in
the manifest and do not satisfy the release gate:

```bash
./scripts/run-acceptance-e2e.sh --playwright-project patrol-admin --disposable
```

## Focused Phase 1 gate

The focused Phase 1 gate runs the physical lost-receipt/query scenario and the
contract-drift/renewed-approval scenario, plus bootstrap and cleanup:

```bash
./scripts/run-acceptance-e2e.sh --disposable --phase1-browser
```

It writes `phase1-manifest.json` and requires all four browser nodes and the six
strict consumers to pass without skips or retries. Its acceptance-only shell
resolver checks the original physical receipt and target; production shell
operations retain their manual reconciliation contract. This profile omits
AC21 capacity preparation and does not satisfy the complete release gate.

## Product coverage

| Project         | Product boundary                                                                                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `identity`      | Login/logout, teams, invitations, workspace isolation, anonymous denial                                                                                                     |
| `control-plane` | Inference endpoint/model/probe/bindings/capabilities and Runtime Policy CAS/history/restore                                                                                 |
| `resources`     | Keyword-only knowledge-base build, READY publication and pinning; malformed PDF yields DOCUMENT_PARTIAL while valid content remains retrievable                             |
| `execution`     | Agent/Ask, SSE, approvals, rejection, cancellation, Sandbox drain, execution workbench, evaluation batches/governance, analysis, comparison, exports, and capacity evidence |
| `patrol-admin`  | Formal Patrol validation/execution/evidence/admission, administration, compliance, mobile and keyboard access                                                               |

`contracts/acceptance-evidence.schema.json` is the source of truth for required
acceptance IDs. The zero-skip reporter fails on a missing, duplicated, skipped,
interrupted, or failed required ID.

Bootstrap probes and binds both chat and embedding models. Only chat becomes
available under the new installation policy; embeddings and reranking remain
disabled until their consumers are explicitly enabled. Resource scenarios keep
that basic policy throughout, without modifying global enhancement settings.

## AC21 capacity prerequisite and current status

AC21 validates retained reference-machine measurements, not the deterministic
provider smoke tests. Full or `execution` runs require both
`ACCEPTANCE_CAPACITY_REPORT` and `ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST`. The
consumer accepts report version 4, role schema 3 and fixture manifest version 1;
its validation receipt is version 2. Successful validation also requires the
runner-owned concrete `OfflineProofContext`, including retained original ledgers,
trusted image locations and the original memory-only signing materials. A public
report or paths alone cannot supply that authority.

The current shell/CLI entrypoint does not construct this private context. The
reference coordinator also rejects a second physical round until the actual C2c
cleanup/reuse gate is connected. The full Linux reference-machine native
scheduler/collector and bounded offline consumer have not been completed.
Consequently the canonical command runs product checks and cleanup while
retaining the real AC21 failure and a nonzero exit; it does not currently establish
a passing full AC21 release gate. Assess functional projects and the six strict
consumers from this invocation's real producer, validated restoration and retained
artifacts. Test collection (`--list`), mocks, helper/unit tests, partial runs,
retained screenshots and zero owned residue do not establish full-stack success
or substitute for the reference measurement. Keep the failed capacity receipt and
manifest intact. See [capacity tooling](../scripts/execution_capacity/README.md)
and [reference protocol](../scripts/execution_capacity/REFERENCE.md).

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

Public cleanup preserves immutable published history. AC18 binds its owned team's
creator, owner, recording job, source run and fixed result metadata when the
recording is published. Only an exact `409 resource_pinned` response followed by
current team/owner membership checks and scoped recording status/result reads can
complete that journal entry as `retained-published-history`. The result endpoint
validates the recording's current fixed pins. Evidence states
`physically_deleted=false` and the permanent obligation to retain the team scope
and owner access; it does not claim the team was deleted or archived. An unbound
conflict, changed identity/owner/version, unavailable recording, server error or
rejected batch archive remains a pending cleanup obligation and fails cleanup.
An explicitly bound team's unexpected deletion or disappearance also remains
pending because its promised permanent history cannot be verified.
Docker residue counts describe invocation-owned infrastructure, separately from
this retained product history. `--disposable` removes the invocation's volumes
after these public dispositions have been recorded.

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
