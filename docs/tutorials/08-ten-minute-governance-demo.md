# Ten-Minute Governance Demo

[简体中文](08-ten-minute-governance-demo.zh-CN.md)

This Compose demo covers a read-only Patrol, a Finding, a persisted approval,
chain verification, and signed evidence.

## 1. Start and seed

```bash
./scripts/quickstart.sh --demo
```

The demo profile starts Ops Collector and OpsConsole, registers
read-only Collector policies, and creates **Demo Governance Patrol**. Configure
a tool-capable chat binding in Settings → Inference if the optional
`DEMO_INFERENCE_*` seed values were not supplied.

## 2. Run Patrol

Open `/patrols`, select the demo Pack, and click **Run now**. The three checks
should pass. Then manufacture a deterministic dependency Finding:

```bash
docker compose stop ops-console
# Run the Pack again and wait for the warning Finding.
docker compose start ops-console
```

The Collector returns registered evidence; the server assertion engine, not an
LLM, determines the failed check.

## 3. Approve one browser action

1. Select **Web Operator** on the home page.
2. Ask it to open `http://localhost:9099` and sign in.
3. Keep exact allowed hosts `ops-console, localhost` and declare the target
   enterprise-owned.
4. When an interactive browser Activity requests approval, inspect its frozen
   tool/risk details and click **Approve**.

The button submits a dedicated approval command. It does not insert an approval
phrase into chat, and it authorizes only that persisted invocation.

## 4. Verify evidence

- `/admin/governance` shows approval/Activity and Patrol trends.
- `/admin/audit` verifies the append-only audit hash chain.
- `/admin/compliance/sessions/{sessionId}` shows formal Run, approval, and
  Activity timelines plus execution-chain verification.
- `/admin/compliance` downloads a ZIP with manifested file digests and
  `chain-signature.txt`.

The Compose demo has no Kubernetes mutation target. For the real
proposal → approval → Actuator → verification loop, use
[approved remediation](07-approved-remediation.md) on a disposable cluster.
