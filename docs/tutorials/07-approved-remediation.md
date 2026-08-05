[简体中文](07-approved-remediation.zh-CN.md)

# Approve an Ops Patrol remediation

Tutorial 06 covers read-only checks only. This tutorial adds a narrow, human-approved write path on top of the same Pack: a separate Ops Actuator executes exactly three registered actions — restart, scale, rollback — against Kubernetes Deployments/StatefulSets, and only after an operator approves the specific call inside a governed session. Nothing outside that approved session ever calls the Actuator.

## Before you start

You need everything from [Read-only daily Ops Patrol](06-ops-patrol.md) already working, plus:

- an Ops Actuator deployed in the target cluster with its own least-privilege ServiceAccount (`get`/`list`/`watch`/`patch` on Deployments/StatefulSets, `get`/`list` on ReplicaSets — never Secrets, `exec`, or `attach`);
- the Actuator registered as a streamable-HTTP MCP Server named exactly `ops-actuator` (the execution service resolves it by this fixed name; unlike the Collector, which is bound per-Pack, there is one Actuator per platform deployment);
- the built-in `ops-patrol-remediation` Skill, which is seeded automatically on API/Worker startup — no manual registration step;
- an administrator who enables the global feature flag `enable_ops_patrol_remediation`, in addition to `enable_ops_patrol` from tutorial 06;
- Operator access to propose a remediation. Approval happens inside the session that proposing creates, so the approver needs ordinary access to that session; Auditors can review the outcome afterward but cannot propose or approve.

For a transport-only local check:

```bash
docker compose --profile actuator up -d --build opencitadel-ops-actuator
```

The Compose profile does not mount a host kubeconfig, same as the Collector. Use the Helm/Kustomize ServiceAccount for real Kubernetes writes, and never expose the Actuator publicly.

## Prepare the Actuator

Configuration is environment-only (`OPS_ACTUATOR_` prefix). The allowlist below is what actually gates every write call — a workload not listed here is denied before any Kubernetes call, regardless of what a session proposes:

| Variable | Default / range | Purpose |
|----------|------------------|---------|
| `TARGET_REF` | `opencitadel-local` | Stable identity matched by the Pack |
| `ALLOWED_NAMESPACES` | `["opencitadel"]` | Non-empty namespace allowlist |
| `ALLOWED_WORKLOADS` | `{}` | JSON map of namespace → workload id → `{kind, min_replicas, max_replicas}`; a workload not listed here cannot be targeted by any write action |
| `TRANSPORT` | `streamable-http` | `streamable-http` or development-only `stdio` |
| `CONCURRENCY` | `4`, range 1–8 | Maximum simultaneous write actions |

Full reference, including output caps, is in the [Ops Actuator README](../../ops-actuator/README.md#configuration-reference).

Example Helm values:

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

In **Settings → Integrations**, register the internal URL (for the default Helm release, `http://opencitadel-ops-actuator:8091/mcp`) as streamable HTTP, enable it, and name it `ops-actuator`. Unlike the Collector, the Actuator's write tools are never exposed to a model — the backend execution service calls them directly — so there is no separate Tool Policy payload to persist for this registration.

As an administrator, open **Settings → Runtime → feature_flags** and enable `enable_ops_patrol_remediation`.

## Propose a remediation

1. Open a Run with an actionable Finding, then go to **Ops Patrol → Runs → {run}**.
2. On the Finding card, select **Propose remediation**. Only Findings backed by a `k8s_*` probe (workload availability, restart spike) offer an automated action; checks such as HTTP, TLS, backup, or dependency probes show "This check has no automated remediation action available" instead.
3. Choose the action — **Restart workload**, **Scale workload**, or **Rollback workload**. `Scale workload` requires a positive-integer replica target; `Rollback workload` accepts an optional target revision.
4. Confirm or fill in the workload name. If the check's probe never recorded one, the field is required before you can submit.
5. Check **I have reviewed the impact summary and rollback plan, and confirm this remediation should proceed**, then select **Propose and open approval session**.

Submitting creates a `PatrolRemediation` record (status `proposed`) and opens a new session running the built-in `ops-patrol-remediation` Skill with `gate_profile=strict`. You are redirected to that session automatically.

## Approve and execute

The new session has exactly one tool available, `patrol_execute_remediation`, declared with `approval=always` — every call is queued for a human decision before anything runs, with no MCP, A2A, memory, or other tool access in the session. The agent's own turn states the action, target, impact summary, and rollback hint in the chat transcript, then calls the tool once; read that message for the plain-language explanation, since the approval card itself shows only the raw tool call.

When the call reaches you, the session shows a **Tool action requires approval** card with:

- the tool name, `patrol_execute_remediation`;
- a raw JSON preview of the call arguments;
- **Approve** and **Reject** buttons.

Selecting **Reject** opens a required reason field; confirming it delivers the rejection to the agent as the tool result. Because this Skill is restricted to a single call, the session then runs to its own normal conclusion rather than being force-killed. Once the session reaches any terminal state, a remediation still sitting in `proposed` is automatically moved to `cancelled` with `error_code=SESSION_TERMINATED` — the Actuator was never called at any point in this path. Selecting **Approve** lets the call proceed.

**Before your decision, zero calls have been made against the Actuator for this proposal.** The tool's approval mode is `always`, so the batch executor queues the call and never invokes it ahead of your decision — abandoning or rejecting the session leaves the Actuator untouched. Once you approve, the service re-verifies the proposal's parameter hash, confirms the Actuator's live capability hash still matches the baseline captured when this session was built (rejecting on drift), then calls the Actuator exactly once using the remediation's own persisted idempotency key — never a value the tool call itself carries. The remediation record moves `executing` → `executed`, or `failed` with a stable error code if any of those checks or the Actuator call itself fails.

For the full governance record of this specific session — the approval decision, tool-invoke chain, and evidence integrity — switch to **Admin → Compliance → {session}** (`/admin/compliance/sessions/{sessionId}`).

## Verify the loop

An `executed` remediation automatically triggers a new Run against the same Pack (`trigger_type=remediation`); no manual re-run is needed. Open the run from the **Remediations** panel's **View recheck run** link on the original Run detail page.

- If the recheck Run's matching check now passes, the remediation becomes `verified` and the original Finding is automatically resolved (`decided_by=system:remediation`), citing both the remediation and the recheck Run.
- If the check still fails or warns, the remediation becomes `failed` (`error_code=recheck_failed`) and the Finding stays open for a human decision.

Download the recheck Run's evidence ZIP and verify its SHA-256 manifest/HMAC exactly as in tutorial 06 — a remediation recheck produces ordinary patrol evidence, with no separate export path. The originating remediation session has its own evidence and approval record, reachable from the governance profile page above.

To exercise the whole loop locally without a live cluster incident, set `PATROL_RUN_REMEDIATION_FIXTURE=true` before running `./scripts/run-patrol-fixtures.sh`. Fixture 21 (`fixture-remediation-crashloop`) injects a bounded crash loop and expects `restart_workload` to move through `proposed → executing → executed → verified`, with both `k8s-workload-availability` and `k8s-restart-spike` passing on recheck.

## Safe rollback

Set `feature_flags.enable_ops_patrol_remediation=false` in global Runtime settings. New proposals are rejected immediately — `propose()` fails closed on this flag before touching anything — while read-only Ops Patrol from tutorial 06, existing remediation history, and evidence remain fully readable. Re-enabling the flag does not discard any `PatrolRemediation` records.

Disabling `enable_ops_patrol` (tutorial 06's flag) also stops remediation, since propose and execute both depend on the same Patrol Run/Finding machinery.

See [Ops Patrol architecture — Remediation](../architecture/ops-patrol.md#remediation) for the trust boundary and state machine, and [Ops Patrol operations](../operations/ops-patrol.md) for deployment and evidence details.
