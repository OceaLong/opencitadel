# Execution Kernel Greenfield Cutover Evidence

This record captures the acceptance evidence for the destructive, single-runtime
cutover completed on 2026-08-25. The target is a new installation; no database,
API, event, deployment, or runtime compatibility with predecessor builds is
provided.

## Final architecture boundary

- PostgreSQL Commands, Events, Inbox, Outbox, Activities, Timers, Snapshots, and
  formal projections are the only durable execution protocol.
- `api/app/execution_kernel_main.py` is the only execution runtime entrypoint.
- Redis is wake-up and notification infrastructure only. Losing Redis state does
  not lose accepted work or workflow state.
- Agent, Ask, knowledge ingestion, codebase indexing, automation, patrol, and
  remediation all use the same Run aggregate and command path.
- Approval is a formal command/state transition. No predecessor execution or
  human-intervention lifecycle remains in production code.

## Destructive schema contract

The repository has one Alembic head: `0001greenfield_initial`. It creates a new
schema and does not upgrade predecessor data. PostgreSQL provisioning installs
`vector`, `uuid-ossp`, and `pgcrypto`, then separates three login roles:

| Role | Responsibility |
| --- | --- |
| Application | Product reads/writes allowed by RLS; no execution append or DDL authority |
| Migration | Schema and extension migration only |
| Execution kernel | Event append and formal execution projections; no migration authority |

The integration suite exercises a fresh PostgreSQL installation, forced RLS,
cross-tenant denial, stream owner matching, immutable event/audit rows, hash
tamper detection, snapshot corruption fallback, and role grants.

Patrol and remediation product records cannot bypass formal admission: every
persisted Patrol Run has a non-null formal Run identity. Session turn admission
is serialized by a PostgreSQL row lock, keyed by an immutable request UUID, and
covered by a real two-request concurrency test. A real execution-kernel process
startup exercises the asynchronous composition root and passes readiness with
the dedicated login role; the probe verifies migrated tables, append privileges,
and the absence of event mutation privileges.

## Verification results

| Gate | Result |
| --- | --- |
| API full suite | 1,279 passed, 5 skipped |
| Sandbox | 32 passed |
| Ops Collector | 33 passed, 2 skipped |
| Ops Actuator | 31 passed |
| UI unit suite | 36 files, 133 tests passed |
| UI lint / typecheck / production build | Passed |
| UI localization | 1,565 keys aligned; 1,281 code-referenced keys present in English and Chinese |
| Import boundaries | 515 files / 1,924 dependencies; 5 contracts kept, 0 broken |
| Import waivers | Zero `ignore_imports`; CI zero-waiver contract passed |
| Python CI undefined-name gate | `ruff check --select F821 app tests` passed |
| New execution-kernel code | Full Ruff rules passed |
| Deployment | API/kernel image builds, Compose config, Helm lint/template, and both Kustomize renders passed |
| Documentation | Bilingual and architecture contract checks passed |
| Patch integrity | `git diff --check` passed |

The UI localization tool reports unused-key inventory as a warning, not a
missing/mismatched-key failure. The UI build reports Node's third-party
`module.register()` deprecation warning. The API suite reports five import-time
deprecation warnings from the third-party PyMuPDF/SWIG package; application
deprecation warnings are clean.

## Negative residue proof

The greenfield boundary contract rejects predecessor class names, module paths,
tables, deployment workloads, and environment settings. Repository scans find
old execution vocabulary only inside those negative assertions. Formal
`ActivityWorker`, `DecisionWorker`, and `InboxWorker` classes are components of
the single execution kernel, not separate runtime lifecycles.

No files were staged or committed by this cutover procedure.
