[简体中文](07-approved-remediation.zh-CN.md)

# Approve an Ops Patrol remediation

Tutorial 06 is read-only. This tutorial adds a separate write path: a formal
`remediation` Run may invoke one allowlisted Actuator operation only after its
persisted approval is accepted. No model receives Actuator tools.

## Before you start

Complete [Read-only daily Ops Patrol](06-ops-patrol.md), then prepare:

- an Ops Actuator with a dedicated least-privilege ServiceAccount;
- a streamable-HTTP integration named exactly `ops-actuator`;
- `patrol_policy.remediation=enabled` in global Runtime Settings;
- an Operator who can propose and approve; Auditors remain read-only.

The Actuator may `get/list/watch/patch` only registered Deployments and
StatefulSets, and `get/list` ReplicaSets. It must never read Secrets or use
`exec`/`attach`.

## Configure the Actuator

All Actuator configuration uses the `OPS_ACTUATOR_` environment prefix. The
allowlist, not proposal text, is the final target boundary.

```yaml
opsActuator:
  enabled: true
  targetRef: production-cluster-a
  allowedNamespaces: [opencitadel]
  allowedWorkloads:
    opencitadel:
      opencitadel-api:
        kind: deployment
        min_replicas: 2
        max_replicas: 10
```

Register `http://opencitadel-ops-actuator:8091/mcp` under the exact name
`ops-actuator`. The backend Activity calls it directly; do not expose it in an
Agent Skill or model tool policy.

## Propose

1. Open an actionable Finding under **Ops Patrol → Runs**.
2. Select **Propose remediation**.
3. Choose restart, scale, or rollback. Scale requires a positive replica count;
   rollback always targets the immediately previous revision.
4. Confirm the exact workload, impact summary, and rollback guidance.
5. Submit the proposal.

The API persists `PatrolRemediation(status=proposed)`, creates its linked
Session, and admits a `remediation` Run. Its single `remediation.execute`
Activity is not created until the workflow has emitted a formal approval
request.

## Approve or reject

Open the linked Session. The approval card shows the subject
`remediation.execute` and its frozen risk summary.

- **Approve** emits an owner-scoped `DecideApproval` command. The Run resumes
  and schedules the Activity.
- **Reject** requires feedback, emits the same command with `rejected`, and
  cancels the Run with reason `approval_rejected`.

Before approval, the Actuator call count for this proposal is zero. On
execution, the service revalidates owner binding, immutable parameter hash,
live capability baseline, action/target allowlist, and persisted remediation
idempotency key. It then moves `proposed → executing → executed`, or records a
stable failure code. Duplicate Activity delivery cannot create another
mutation.

The governance profile under **Admin → Compliance** shows the approval
identity, decision actor, feedback, Run/Event chain, and evidence.

## Verify the loop

An executed remediation automatically admits a verification Patrol Run against
the same frozen Pack.

- A passing linked check moves the remediation to `verified` and resolves only
  its original Finding.
- A warn/fail/error result moves it to `failed` with `recheck_failed`; the
  Finding stays open.

Download the verification Run evidence ZIP and verify its manifest hashes and
HMAC as in tutorial 06. The remediation Run and approval remain separately
visible in the governance profile.

For a disposable cluster exercise:

```bash
PATROL_RUN_REMEDIATION_FIXTURE=true ./scripts/run-patrol-fixtures.sh
```

The fixture validates the real Collector, Actuator, Kubernetes mutation,
idempotent replay, and verification path without an LLM.

## Disable safely

Set `patrol_policy.remediation=disabled`. New proposals and execution fail
closed; read-only Patrol, prior remediation Runs, approvals, audit rows, and
evidence remain readable. Set `patrol_policy.admission=paused` to stop all new
Patrol and remediation Runs while retaining navigation and history.

See [Ops Patrol architecture](../architecture/ops-patrol.md) and the
[operations runbook](../operations/ops-patrol.md).
