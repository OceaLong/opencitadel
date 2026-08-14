[简体中文](08-ten-minute-governance-demo.zh-CN.md)

# The 10-minute governance demo loop

This tutorial runs the whole OpenCitadel governance story — read-only Ops Patrol, a manufactured Finding, the governance dashboard, a human-approved tool call, audit-chain verification, and a downloadable evidence package — end to end on plain Docker Compose, with no Kubernetes cluster required. It builds directly on [tutorial 06](06-ops-patrol.md) and [tutorial 07](07-approved-remediation.md); read those first if you want the full mechanics behind any step here.

Everything below uses `./scripts/quickstart.sh --demo`, which brings up a self-contained demo target (a bundled `ops-console` app standing in for a real internal system) and seeds it automatically. Total time: about 10 minutes, most of it spent waiting for the first Docker build.

## Before you start

You need:

- Docker Desktop or Docker Engine + Compose v2, 8 GB RAM minimum;
- nothing else already bound to ports 8088 (OpenCitadel), 9099 (the demo Ops Console), 8090 (Ops Collector), or 8091;
- optionally, an OpenAI-compatible LLM base URL + API key, if you want step 1 to register a working model automatically (otherwise you add one by hand in step 2, same as [tutorial 01](01-self-host-10-minutes.md)). Step 6 needs *some* tool-capable default model either way.

## 1. Start the demo stack

```bash
git clone https://github.com/OceaLong/opencitadel.git
cd opencitadel
./scripts/quickstart.sh --demo
```

`--demo` merges `patrol` and `demo` into `COMPOSE_PROFILES` on top of whatever `local` sets, so `docker compose up -d --build` also starts the Ops Collector (`opencitadel-ops-collector`) and the bundled Ops Console (`opencitadel-ops-console`, the same app tutorial 05 uses for refund reconciliation). Once both report healthy, the script runs `python -m app.seed_demo` inside the API container for you. That module:

1. enables `feature_flags.enable_ops_patrol`;
2. enables the `ops-collector` MCP Server and writes all nine of its read-only Tool Policies (the exact payload from [Ops Patrol operations](../operations/ops-patrol.md#register-the-mcp-server));
3. optionally registers a demo LLM endpoint/model and sets it as the system default (see below);
4. creates, validates, and activates a custom Pack named **Demo Governance Patrol** (`demo-governance-patrol`) with three checks: PostgreSQL dependency health, OpenCitadel API endpoint health, and Ops Console dependency health.

Every step is idempotent — re-running `docker compose exec -T opencitadel-api python -m app.seed_demo` (the script prints this exact command if seeding fails, e.g. because a container was not yet healthy) makes zero additional writes once the state already exists, and prints `[skip] ...` instead.

**To auto-register a model (optional):** the API container loads `.env` via Compose's `env_file:`, so add these four lines to `.env` *before* the containers start (edit it during the "Press Enter when .env is ready" pause, or beforehand if `.env` already exists from a prior run):

```bash
DEMO_LLM_BASE_URL=https://api.example.com/v1
DEMO_LLM_API_KEY=sk-...
DEMO_LLM_MODEL=gpt-4o-mini
DEMO_LLM_PROVIDER=openai   # optional, defaults to openai
```

If any of the first three is missing, `seed_demo.py` skips this step entirely (prints a reminder) rather than guessing — add a model by hand afterward in **Settings → Models**, exactly as in tutorial 01's step 4.

## 2. Log in and confirm the seed

Open **http://localhost:8088** and log in with `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` from `.env`.

Open **Ops Patrol** (`/patrols`). You should see one Pack, **Demo Governance Patrol**, with status **Active** and three checks. If you set the `DEMO_LLM_*` variables in step 1, **Settings → Models** already shows a default model named "Demo Model" under endpoint "Demo Endpoint" — otherwise add one now, since step 6 needs a tool-capable default model to run a session at all.

## 3. Run now — all green

Open the Pack (`/patrols/{id}`) and click **Run now**. The UI attaches a unique `Idempotency-Key`, so a duplicate click never creates a second Run. Wait for the Run to reach a terminal state; all three checks pass, since the Ops Console container just started:

| Check | Probe | What it observes |
|-------|-------|-------------------|
| `dependency-health` | `dependency_status` on `primary-dependencies` | The platform's own PostgreSQL |
| `endpoint-health` | `http_probe` on `primary-endpoint` | The OpenCitadel API's `/api/status` |
| `demo-console-health` | `dependency_status` on `demo-console-tcp` | A TCP dial to the Ops Console container |

## 4. Manufacture a Finding

```bash
docker compose stop ops-console
```

Go back to the Pack and click **Run now** again. This time `demo-console-health` fails, and a **warning**-severity Finding appears on the Run.

Why this specific check, and why it fails deterministically rather than erroring out: stopping the container makes the TCP dial refuse. The Collector's `dependency_status` tool catches that refused connection internally and still returns a normal envelope — `status="ok"` with `data.healthy=false` — so the server-side assertion engine evaluates `$.healthy eq true` as configured, finds it false, and turns it into a `FAIL` result at the check's own `severity_on_fail` (`warning` here). By contrast, an `http_probe` against a fully-stopped target lets the connection error propagate and short-circuits to a generic `ERROR` *before* any assertion runs — technically still a Finding, but not a demonstration of the Pack's own configured assertion/severity. That is exactly why this Pack's third check targets `demo-console-tcp` via `dependency_status` rather than the `demo-console` HTTP probe.

Restore the container before moving on — step 6 needs it running:

```bash
docker compose start ops-console
```

## 5. Look at the governance dashboard

Open **http://localhost:8088/admin/governance**. The **Ops Patrol trend** chart (Runs vs. Findings) already reflects the two Runs from steps 3–4. The **Pending approvals**, **Interceptions**, and **Approval outcomes** panels are still empty at this point — they populate once you generate a gate decision in the next step, so it is worth returning here afterward.

## 6. Trigger and approve a governed tool call

The Ops Console is also a stand-in for "some internal system an Agent should only touch under supervision." From the home page:

1. Pick the **Web Operator** skill.
2. Send a prompt such as: *Open the Ops Console at http://localhost:9099 and sign in.*
3. The **Web Operator scope** dialog opens before the session is created. Leave **Allowed domains** at its default (`ops-console, localhost` — `localhost` already covers this demo target, so the first navigation will not itself need approval). Set **Gate profile** to **Strict**, then click **Start Web Operator session**.

Gate profile matters here: `standard` only gates a risky tool call when its arguments match a critical-action keyword list (delete/close/refund/…), which the Ops Console's login form does not. `strict` gates *every* call that matches the risk list — which includes `browser_click` and `browser_input` — unconditionally, so the very first click the agent makes is guaranteed to pause for approval; that determinism is why this demo asks for Strict specifically rather than relying on a keyword match.

Watch the session: the agent states its plan, then its first `browser_click`/`browser_input` call produces a **Tool action requires approval** card (tool name plus a raw JSON preview of the arguments). Click **Approve**. Mechanically this sends the literal chat message `approve` into the session — the button is a shortcut for typing it yourself — so the transcript shows a normal user turn, not a hidden side channel. If the agent proposes further gated calls, either approve them one at a time or use **Approve same tool** to stop being asked for that tool for the rest of the session.

## 7. Verify the record

Open **http://localhost:8088/admin/audit** and click **Verify chain**. It should report **Audit chain intact**. Browse the log list and open one entry to see its actor, chain sequence number, and metadata.

Open **http://localhost:8088/admin/compliance**. Your Web Operator session appears in **Evidence sessions** (any session with an operator scope or gate profile qualifies) with its **Scope**, **Gate**, and **Chain** columns filled in. Click that row's **Verify chain** button for a session-scoped check. For the full governance dossier — approval decisions, the tool-invoke chain, checkpoints, and evidence-integrity status — open `/admin/compliance/sessions/{sessionId}` directly, using the session ID from the row's link (or from the session's own URL, `/sessions/{sessionId}`, while you still have it open).

## 8. Download the evidence package

Still on `/admin/compliance`, click **Download ZIP** on that same session's row. The archive carries the session's audit material, a `manifest.json` file-hash listing, and `chain-signature.txt` — see [Ops Patrol operations — Evidence verification](../operations/ops-patrol.md#evidence-verification) if you want to verify the HMAC offline with `AUDIT_SIGNING_KEY`.

## 9. Advanced: approval-gated remediation

Everything so far was read-only observation plus a governed *read* of a third-party UI — no automated repair action ran. Ops Patrol's write path (propose → approve → execute → recheck) only offers a remediation action for Findings backed by a `k8s_*` probe (workload availability, restart spike): the Ops Actuator restarts, scales, or rolls back real Kubernetes Deployments/StatefulSets, and this Compose demo profile has no cluster for it to act on — the checks seeded here (`dependency_status`/`http_probe`) intentionally have no automated action, by the same design covered in tutorial 06.

To see that loop end to end, follow [tutorial 07](07-approved-remediation.md) against a disposable `kind` cluster. The same flow (fail → propose → execute → recheck) is what CI now verifies deterministically on every run, through two independent layers: a real `kind` cluster driving the actual Ops Actuator MCP server, and an LLM-free, in-process replay of the propose/execute/recheck state machine. Neither CI layer exercises the approval step itself — that's covered by contract tests and end-to-end runs against a protected environment. See [Ops Patrol operations](../operations/ops-patrol.md) for the current CI coverage.

## Next

- [Tutorial 06: Read-only daily Ops Patrol](06-ops-patrol.md)
- [Tutorial 07: Approve an Ops Patrol remediation](07-approved-remediation.md)
- [Ops Patrol operations](../operations/ops-patrol.md)
