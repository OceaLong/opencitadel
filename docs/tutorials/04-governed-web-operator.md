# Governed Web Operator

[简体中文](04-governed-web-operator.zh-CN.md)

This tutorial uses the bundled OpsConsole as an enterprise-owned browser
target.

## Start

```bash
cp .env.example .env
# Set required secrets and configure a tool-capable model.
docker compose --profile local --profile demo up -d --build
```

OpenCitadel is at `http://localhost:8088`; OpsConsole is at
`http://localhost:9099` (`agent` / `agent123`).

## Run

1. Select the **Web Operator** Skill.
2. Ask it to open OpsConsole, sign in, inspect a ticket, and make a chosen
   update.
3. In the ownership dialog choose **Enterprise-owned** and keep exact allowed
   hosts `ops-console, localhost`.
4. Start the session.

Every browser navigation is checked against the frozen host list. Read-only
page inspection proceeds under read policy. Navigation, click, input, and
other interactive calls create a persisted approval card. Review the frozen
tool/risk details and approve or reject through the card. Approval is a
dedicated command, not a chat phrase.

Use VNC when you need to inspect or directly interact with the isolated
browser. VNC interaction does not mark the Agent's pending Activity complete;
the Run continues only through its formal result/decision protocol.

## Verify

After the Run terminates:

- open `/admin/audit` and verify the audit chain;
- open `/admin/compliance/sessions/{sessionId}` for Run, approval, and Activity
  timelines;
- download the evidence package from `/admin/compliance` and inspect
  `manifest.json` plus `chain-signature.txt`.

To schedule the same task, create an Automation job with the Skill, exact
Operator domains, model, and optional resource bindings. Each firing creates a
formal Automation Run linked to an Agent Run.

See [Web Operator architecture](../architecture/web-operator.md).
