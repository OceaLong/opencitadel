# Ops Patrol Architecture

[简体中文](ops-patrol.zh-CN.md)

Ops Patrol separates read-only collection, deterministic assertion, and
approval-gated remediation. Patrol and remediation use the universal execution
kernel; neither has a private task lifecycle.

![patrol read flow](../assets/diagrams/patrol-read-flow.png)

![remediation flow](../assets/diagrams/remediation-flow.png)

## Patrol Packs and collection

A Pack has a mutable versioned definition. An edit requires `expected_version`,
increments its version, returns it to `draft`, and clears validation. A formal
Patrol validation Run must complete for that version before activation.
Execution admission copies assertions, target, collector server ID, capability
hash, and enabled tools into an immutable product Run snapshot; the formal Run
input links that snapshot by Patrol Run/Pack identity. Retention is live
Operations Policy, rather than part of the Pack snapshot. Collector output must match the registered closed-world schema and the
frozen capability hash.

The Collector owns read-only Kubernetes/HTTP/Prometheus/certificate/backup/
dependency probes. It accepts only configured names and destinations. The
kernel validates every submission before deterministic server-side assertion;
LLM output cannot decide pass/warn/fail.

`PatrolExecutionActivityHandler` is idempotent: finalization uses the Run's
submission key and creates one report/finding set. Evidence references and
digests are stored before the Activity reports success. Signed evidence is
assembled by the separate evidence-export endpoint; assertion success itself
does not create a signed archive.

## Remediation

A Finding may produce a remediation proposal from a fixed action policy.
Currently only `k8s_*` probe findings have remediation actions; HTTP, certificate,
backup, dependency, and Prometheus probes have none. Restart and rollback accept
no action parameters; scale accepts a positive integer `replicas`, and rollback
targets the immediately previous workload revision. The
proposal becomes a linked `remediation` Run whose single
`remediation.execute` Activity always requires formal approval. The approval
freezes subject and risk information; only a dedicated approval command can
advance it.

Remediation defaults to `disabled`; `propose_only` permits proposal review
without execution, and `enabled` admits execution subject to approval.
The Actuator exposes registered restart, scale, and rollback-style operations
within explicit namespace/workload allowlists. It has separate ServiceAccount,
NetworkPolicy, non-root/read-only container hardening, and idempotency keys.
It cannot read application credentials or issue arbitrary Kubernetes calls.

After execution, a linked verification Patrol Run determines whether the
finding is resolved. Remediation status is projected from these durable Runs,
not from transport success.

## Safety invariants

- Collector has no write RBAC; Actuator has no arbitrary read/write API.
- Capability drift, owner mismatch, unregistered target, invalid evidence, or
  missing approval fails closed.
- A rejected/cancelled/expired approval makes zero Actuator calls.
- Duplicate trigger, Activity delivery, or completion cannot create another
  finding set or mutation.
- Audit and evidence rows outlive product retention; cleanup removes only
  expired product references allowed by policy.

See [governance plane](governance-plane.md), [security model](security-model.md),
and [Patrol operations](../operations/ops-patrol.md).
