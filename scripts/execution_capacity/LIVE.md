# Finite live source workload (B3b)

This document describes the implemented finite live workload, not a completed
full-capacity measurement. The
standard corpus remains 100,000 Runs / 10,000,000 formal events; its completed
5,000-result batch and separate 30,003-event / 10,000-step probe are not live load.
The reference modules implement host/guest protocol, network/calibration,
original diagnostics and evidence replay. The complete cold-reset/native client
scheduler and collector, multi-round cleanup/reuse connection and full reference
acceptance remain outstanding; helper receipts do not prove these runtime gates.

The current release gate status and private proof prerequisites are described in
[the acceptance README](../../e2e/README.md#ac21-capacity-prerequisite-and-current-status).

## Fixed runtime composition

Use the existing `compose.yml` together with `compose.live.yml` only for the
live deployment after the corpus is sealed. It keeps the full normal API/kernel
entrypoints, real-test Minio factories, all decision/activity/heartbeat/evaluation
lanes, metrics and shutdown. The live overlay specifies three full kernel
replicas and claim batch 8, preserving the existing per-process concurrency 8
and pool 5+5. It does not raise any physical, user, evaluation or global cap.
Other valid settings must be provisioned explicitly and match the trusted binding;
`validate_topology` rejects a claim batch above actual execution concurrency or
insufficient worker/pool/physical headroom. At default subject5/judge2, 18 handler
slots include ten streams plus one ordinary probe. Three replicas provide24;
this arithmetic is only a prerequisite, never proof of active work.

Every kernel must mount the same operator-created 0700 private directory at
`/capacity-live` writable, matching its existing UID/GID. The original read-only
`/capacity` and `/capacity-binding.json` and evaluation inventory/budget mounts
remain. Real Minio still requires the exact existing bucket, no provisioning.
The source digest includes Python source, both overlays, provider library modules
and server. Provider image contents must independently match this source during
C's actual container/image/command/environment/mount/ownership readback. The
source-only driver cannot establish host containment from its own binding.

Trusted `binding.live` adds:

- `profile`: exactly `finite-text-120x500ms-v1`.
- `workers`: actual count; `worker_hostnames`: exact owned container hostnames.
- `suite_version`: actual published isolated 1000×5×1 suite from the corpus.
- `completed_corpus_batch_id`: the completed corpus batch identity, forbidden as live load.
- `windows`: immutable UUID-keyed plans, each with `startup_seconds` (default
  protocol proposal10; bounded3–20), `seconds` (integer2–30), and exactly ten
  unique preregistered session prerequisites. Each prerequisite has exactly
  `session_id`, `session_created_at`, `model_id`, `endpoint_id`; it cannot override
  principal, policy, DB or deployment authority. Every plan/session is single-use
  including failed attempts. No window or session replacement after results.

Sessions are externally preprovisioned empty personal Ask sessions under the
binding's same actual principal. Existing service authority, exact creation time,
empty history/resources/MCP/A2A/memory and pinned no-vector memory policy are
checked using `verify_prerequisite` before actual `AgentService.chat` admission.
The model must resolve to `acceptance-live`, OpenAI-compatible provider, the exact
owned endpoint, output bound600–4096, and current activity/model/request deadlines
at least70s. Actual dispatch receipts additionally verify stream=true and the
configured profile. No request text authorizes streaming.

The provider emits120 deterministic text fragments, each after500ms plus any
actual backpressure delay, then stop, actual fixture-defined usage, DONE. Thus
nominal60s is finite, and slow delivery may take longer under real deadlines.
The original acceptance-capacity100ms profile is unchanged and supplies the
independent subject/judge batch. Emission scheduling is not durable rate proof.

## Actual entrypoints and integration

The kernel command stays `python -m scripts.execution_capacity.kernel_main`;
trusted composition enables only ordinary session Ask text streaming. Default
production composition still invokes normally. Tool/evaluation/judge calls keep
invoke. Only supported OpenAI-compatible adapters select the narrow text branch.

Fixed guest source-driver command:

```
python -m scripts.execution_capacity.live_main --window-id <bound-window-uuid>
```

It reads only the fixed private mounts, opens actual resources and shared
services, initializes the policy reader and fixes minimal-ready time. It enters
`LiveWorkload(resources, shared, journal, binding).window(window_id,
minimal_ready_ns=...)`. Standalone mode observes only source load; it deliberately
performs no target UI action and cannot produce a capacity acceptance report.
C should create a new LiveWorkload per window and enter that same context while
performing real native target operations. The context yields exact run IDs,
batch ID, fixed start/end and guest boot ID. No arbitrary success callback is
accepted. Caller operations extending beyond the preregistered window fail.

The controller starts a separate BatchService batch against the real suite and
normal preflight/default settings, not seeded result rows. At fixed startup
schedule it requires all ten real model.call tasks call_started with unexpired
claims/timeouts, matching generation/claim, a real physical dispatch and active
reservation, plus genuine fragment acknowledgements. Claimed semaphore waiters
cannot satisfy this check. It samples same-claim continuity every100ms, including
final two setup seconds, checks unchanged default batch settings, and requires
actual new batch dispatches AND settlements across the window. No mid-window
replacement or paused scheduler. Fixed finite windows can fail on slow setup,
backpressure, rate, lost worker, early batch completion or operation overrun.
These failures must remain in C's complete attempt ledger.

`admission_probe(...)` performs the same actual AgentService path and
acceptance-capacity profile for both baseline and loaded probes, using fresh
explicit session prerequisites. Its conservative elapsed admission-to-persisted
session-attach duration includes readback overhead in both conditions. C owns
preregistration/counts and the <=20% comparison, and must not relabel this guest
service duration as browser-network admission latency.

## Evidence, clocks and failure semantics

`ObservedProgress` is a pass-through wrapper around the real Postgres sink, not
a workload generator. The private FULL-synchronized recovery journal records
submission monotonic ns, event ID, per-claim sequence, safe fragment count, actual
boolean durable ack/error and acknowledgement ns. Journal failure never creates
a positive acknowledgement; missing/failed evidence invalidates measurement.
Normal display-only false ack does not turn a domain response into failure.
Model content, reasoning and tool arguments never appear in incremental progress.
Response-complete100 means only response phase, never Run completion.

The validator requires at least two unique committed effective progress updates
per fixed one-second bin for each of the same ten Runs; no tolerance. Consecutive
sequence and fragment counts, source applied=true and committed public event
identity/message are required. Duplicate ack/identity, gap, stale observation,
failed persistence or missing public evidence fail. Commit time determines the
bin; a submission may start before a bin. Final100 cannot count as sustained load.
Actual current authorized StepView readback must join the same fragment summary
or a later acknowledged fragment of that Run. Public event receipt/readback and
one source-window end-step readback are NOT native DOM/paint evidence; C must
collect actual continuously selected live step visibility and detect lag.

All worker/controller times are Linux monotonic values from the same verified
**guest boot**. They cannot be subtracted from host coordinator or browser
monotonic times across QEMU boots/hosts. C's approved host-local
pre-dispatch→identity-and-paint bound needs its own causal protocol and clock
identity; B3b receipts only establish committed causal identities/order. Do not
subtract clock epochs or assume guest/host monotonic equivalence.

After the window, finite ordinary sends drain through normal workers; the extra
batch receives a real idempotent cancel and normal cleanup/scoring/decision lanes
continue. Real batch terminal/clean, all environment operation receipts and real
ordinary final statuses/physical settlements are read. Local event subscribers
are cancelled only after bounded collection or failure; that is never evidence
that remote effects stopped. Primary and cleanup/shutdown failures are preserved.
All session/admission/batch/dispatch/progress/environment identities and immutable
history remain in the private journal. Unreturned admissions, unknown sends,
nonconverged cleanup and uncertain uploads remain explicit recovery obligations.
C must corroborate broker/host physical absence and upload inventories before
certifying final clean; the B3b disposition explicitly says `pending_C`.

A crashed/failed window is never silently rerun as a replacement sample. Reconcile
its exact journaled requests/sessions and real workers/services, retain pending
identities and immutable source, then start a separately preregistered whole
attempt after authority is restored. No prefix deletion, history deletion,
reservation refund, policy activation or synthetic success is used here.
