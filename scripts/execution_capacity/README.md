# Capacity source construction and evidence tooling

This directory constructs controlled historical retrieval through formal Run
commands and real services. The seed entrypoint requires exactly 100,000 Runs
and 10,000,000 formal events, then constructs a separate 30,003-event /
10,000-step probe and a published 1,000-case × 5-subject × 1-repeat batch
(5,000 results), with actual subject/judge execution and verified environment
cleanup. Historical retrieval is not a natural conversation or model-throughput
measurement. Completion of these source phases yields `corpus_ready` and still
sets `fixture_complete=false`.

[Finite live load](LIVE.md), [Linux reference rounds](REFERENCE.md), original
ledger replay, private proof copying, native evidence validation and failure
retention are also implemented helpers. They do not form a completed full AC21
reference benchmark. `reference_round.reserve_round` still rejects a second
physical round until the C2c cleanup/reuse gate is connected; the full native
scheduler/collector remains unwired. The normal acceptance CLI also does not
construct the mandatory private `OfflineProofContext`. No full-capacity pass can
be inferred from source tests or the version-4 report format.

## Explicit provisioning contract

The operator supplies a private regular 0600 JSON binding and selects its exact
project with `--workspace-prefix`. This is an operator-selected prerequisite,
corroborated by Docker and application/DB readbacks, not a signed creation proof.
Only personal scopes are supported. Required binding fields are:

- `environment`: `test`; `project`: exact dedicated Compose project;
  `invocation` and `fixture_id`: UUIDs; `team_id`: absent or null.
- `network_id`: full Docker network ID. The network must be internal with no
  foreign live endpoints. Every inventoried container must have only this
  configured network, no published ports and no privileged mode.
- `containers`: full container ID to `{service, role, image}` mapping. Images are
  immutable `sha256:...` IDs. Recognized infrastructure services are `postgres`,
  `redis`, `minio`, and `inference-provider`. The exact owned sandbox broker is
  a separately validated capability service; every other service is a producer.
  Labels must bind Compose project/service and
  `com.opencitadel.acceptance.project` / `com.opencitadel.acceptance.run`.
- `kernel_container`, `kernel_image`: exact inventoried kernel and image. Its
  actual environment must include `ENV=test`. Settings and credentials come from
  its private environment, never CLI credentials. No image pulls occur.
- `minio_container`, `minio_endpoint`, `minio_bucket`: exact owned MinIO service,
  internal network alias plus exposed port, and already-existing bucket. Alias
  ambiguity is rejected. The child accepts only MinIO, compares deployment
  settings with this binding, and opts into real test I/O. It cannot provision a
  bucket, fall back to COS, or return fake presigned URLs.
- `readonly_config_mounts`: exact kernel bind destinations, all read-only files
  under `/run/opencitadel/` or `/etc/opencitadel/`. No Docker socket, writable
  kernel volume or arbitrary executable is inherited. The operator must make
  them readable to the host invoking UID, which the child uses.
- `principal_id`, `session_id`, `session_created_at`, `model_id`, `endpoint_id`,
  `policy_revision`: existing active principal and owned personal Ask session.
  Session owner and creation timestamp are read from persistence. This is owner
  evidence, not proof of a nonexistent creator field. The session must be empty,
  pending, without attachments, sandbox, active Run, skill or knowledge bindings;
  memory and all visible MCP/A2A sources must be empty. Real pinned memory policy
  must disable vector recall. Admission still resolves a real model and records
  its signed requester/configuration snapshot; retrieval performs no model send.
- `database_name`, `database_system_identifier`, `migration`: actual database,
  PostgreSQL cluster system identifier and current migration. The kernel DB role
  needs read access to `pg_control_system()` and `pg_stat_activity` sufficient to
  verify all clients; otherwise provisioning is incomplete. No permission grants
  or administration are performed by this script.
- `source_sha256`: result of `scripts.execution_capacity.host.source_digest` on
  this worktree. This binds the mounted Python driver/application/core sources,
  not a complete benchmark dirty-build fingerprint. Both capacity Compose
  overlays and the inference fixture modules/server are included as well.
  Benchmark report binding is separately governed by the shared acceptance
  capacity schema.
- `provider_container`, `provider_base_url`: exact owned deterministic provider,
  unambiguous internal alias, exposed port and `/v1` path.
- `probe`: independent fixture, principal and empty personal session prerequisites
  for the 10,000-step probe; only the bootstrap identity fields may differ.
- `batch`: five distinct subject selections, an independent judge selection,
  exact registered environment and its immutable version, rubric and budgets.
  The real dataset/suite/configuration services validate and publish these
  resources; the entrypoint does not insert completed result rows.
- `broker`: exact running capability-service identity, command, Docker socket,
  durable operation volume, read-only inventory/budget mounts and immutable
  fixture/sandbox/bootstrap image IDs. This authority is confined to the broker;
  the seed child does not inherit its socket.
- `writer_journal_root`: exact preprovisioned private writer directory mounted
  writable at `/capacity-writers` in original API/kernel containers and the
  child. The API/kernel also require the fixed genuine-storage entrypoints and
  matching read-only `/capacity` and `/capacity-binding.json` mounts.

The existing capacity `compose.yml` expresses these mount/entrypoint
prerequisites; it must be supplied alongside the root Compose file. It does not
provision the binding, credentials, principals, test inventory or bucket.

The isolated database must have no unrelated execution streams, batches, exports,
comparisons, physical dispatches, recording/import work, poison markers, resource
GC work or producer schedules. A real execution-slot policy must already be
provisioned and match deployment settings. Admission ceilings are preserved.

## Entry point

With the existing deployment-compatible Python environment and repository root on
`PYTHONPATH`, the source entry point is:

```text
PYTHONPATH=api:. python -m scripts.seed_execution_visualization \
  --workspace-prefix EXACT_PROJECT --runs 100000 --events 10000000 \
  --seed INTEGER --output PRIVATE_INVOCATION_DIRECTORY \
  --target-binding PRIVATE_BINDING_JSON
```

`--help` only parses arguments. Actual invocation stops exact owned producers,
starts the fixed owned child and performs real database, storage, broker and
provider work. Run it only against the explicitly provisioned dedicated test
deployment. Import/help does not install software, start Docker/VMs or call a
provider.

The host stops all originally running exact producers and records original states
before stopping anything. A fixed one-off kernel-image child starts into a stdin
wait; no settings or service resources open until the host verifies its live
network, image, command and mounts. Source and authority input are read-only; only
the exact 0700 invocation and writer-journal directories are writable. Host inspection continues while
the child runs and before each claim; the DB rejects other client addresses/roles.
The driver runs actual admission, current policy timeouts, retrieval handlers,
claims, gate, heartbeat, content preparation, formal worker settlements and
projectors. `formal_now` alone is historical; operational clocks remain current.

## Recovery and honest disposition

`fixture.json` remains the original immutable **planned** ownership contract;
`historical-result.json` is a private, attempt-bound completion receipt. Only real
hash/replay, canonical projection, public step/content parity and exact source
counts can produce `historical_ready`; the probe and actual batch must then
produce `probe_ready` and `batch_ready`. The child records `corpus_ready` only
after all three phases converge; `fixture_complete` remains false. Normal
verified restoration also writes `corpus-result.json`. No measured capacity
report, performance acceptance or full fixture completion is produced by seed
construction. If the optional seal handoff is selected, restoration is explicitly
`withheld_for_offline_seal` until its original coordinator completes the seal;
child exit alone does not authorize producer restoration.

`ownership.jsonl` retains the original lock/identity contract. `recovery.sqlite3`
is private indexed write-ahead storage (FULL synchronized transactions). It holds
raw exact object keys, original command envelopes, configuration/requester proofs,
claim/content/timer selectors and exact physical upload attempt identities. Never
publish this directory, `child.env`, private logs, raw SQL/configuration bodies or
recovery payloads. Safe artifacts may contain only identities/counts and a journal
digest. The journal is not execution authority: recovery reads actual inbox,
source causation, task generation and content parent selectors.

Retrying a response-lost settlement preserves the exact original envelope and
claim. The normal worker settlement UUID has no claim suffix. No fresh worker
claim is allowed after any previous claim generation without authoritative prior
settlement; a result object alone is not a complete outcome. Missing or failed
physical-upload acknowledgements stay unresolved even when an exact object read
is absent, or a later same-key put succeeds. Managed in-process uploads are
shielded and bounded; unresolved durable attempts prevent convergence. No object
or immutable SQL history is deleted.

Run the same command, exact invocation/binding and unchanged source to reconcile
recoverable work. If Docker create lost its response before its ID was recorded,
the exact prewritten child name selects a candidate for full image, attempt labels,
argv, user, mount source/mode, configured network and created-state readback before
its actual ID is acknowledged. Drift or an absent/unverifiable candidate fails
closed and requires operator reconciliation; there is no prefix search or assumed absence. Failed standard construction may
legally cancel owned incomplete Runs, thereby **invalidating** its exact standard
cardinality; these facts are retained and the attempt cannot become ready later.

Producer restoration requires actual child exit and a fresh attempt-bound
convergence receipt. Unknown physical completion, unreconciled work or unverifiable
child death withholds restoration and leaves only the exact owned producers
stopped. Preserve the private inventory, inspect those exact identities, resolve
actual pending envelopes/claims or physical receipts through their real paths,
and obtain authoritative convergence before restarting originally running
producers. Never delete source rows, reset claims, rewrite hashes or purge objects
to make the attempt pass. The script preserves primary/restoration failures and
checks restored health. These paths still require dedicated runtime validation.

### Capacity report compatibility

The bounded consumer writes **report version 4** and **validation receipt version 2**.
Public role artifacts remain version 3 (fixture manifest version 1); the report
version is independent of the private original-journal and C2c projection versions.
A version 3 report or a mixed old/new report is rejected. Regenerate the report
from all retained original artifacts and the trusted private proof context; changing
only its version or supplying aggregate hashes does not migrate evidence.

| Field           | Report 4 representation                                                   | Retained authority                                  |
| --------------- | ------------------------------------------------------------------------- | --------------------------------------------------- |
| Frame intervals | Complete count, exact nearest-rank p50/p95, maximum, ordered JSON SHA-256 | Every selected frame in the original resource trace |
| Long tasks      | Complete count, count above 200 ms, maximum, ordered JSON SHA-256         | All original task intervals                         |
| Retained scopes | Unique scope count and SHA-256 of the original sorted JSON ID array       | Complete cohort scope IDs                           |
| Cohorts         | Count and SHA-256 of the original ordered cohort-summary JSON array       | Every base and round cohort, including all Run IDs  |

An empty statistical population is represented by count zero and null extrema and
percentiles. Empty frames cannot pass acceptance: the original resource gate
requires at least 100 selected frames per trace and the budget gate rejects an
absent p95. Empty long-task populations remain valid. Ordered commitments use
ordinary canonical JSON array bytes (sorted object keys, compact separators,
ASCII escaping); they are recomputed from complete original values, not trusted
as evidence by themselves. Exact numeric ordering does not round integer values
through floating-point storage.

Validation still checks each sample's frame p95 and the pooled p95 independently,
as well as every original identity, ownership, timing and cleanup predicate. The
receipt's frame percentiles are emitted only after a fresh retained-destination
validation matches the complete rederived report. Latency arrays retain every
preregistered sample. These format changes and unit tests do not constitute a
full-population or reference-machine capacity measurement.
