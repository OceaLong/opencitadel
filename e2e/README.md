[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel E2E Tests

Playwright end-to-end smoke tests for the OpenCitadel UI and the **Web Operator demo backend** (OpsConsole).

## Scope

| Suite | File | What it covers |
|-------|------|----------------|
| OpsConsole demo | `web-operator.spec.ts` | Login page, ticket list after login |
| Platform smoke | `web-operator.spec.ts` | OpenCitadel home page loads |

These tests support [Tutorial 4: Governed Web Operator](../docs/tutorials/04-governed-web-operator.md) — run them after standing up the demo stack.

**What is not covered**: settings modal, formal approval flows, team invitations, knowledge-base ingest, codebase flows, admin console, or mobile navigation. UI unit tests live in `ui/src/**/*.test.ts` (logic only, no component regression). Do not treat `npm test` in `ui/` or `e2e/` as full UI coverage.

## Prerequisites

- Node.js >= 22
- Running OpenCitadel stack (default `http://localhost:8088`)
- For OpsConsole tests: demo profile enabled

```bash
# From repo root — start platform + demo OpsConsole
docker compose --env-file .env.e2e build opencitadel-sandbox
docker compose --env-file .env.e2e \
  --profile local --profile demo --profile patrol up -d --build
```

The explicit sandbox build is required: the broker starts dynamic sandbox
containers from `opencitadel-sandbox`, while the Compose service that declares
that image belongs to the inactive `fixed-sandbox` profile.

OpsConsole default URL: `http://localhost:9099` (override with `OPS_CONSOLE_URL`).

## Install and run

```bash
cd e2e
npm ci
npm test
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAYWRIGHT_BASE_URL` | `http://localhost:8088` | OpenCitadel UI base URL |
| `OPS_CONSOLE_URL` | `http://localhost:9099` | OpsConsole demo backend |

## Ops Patrol real-runtime path

`patrol.spec.ts` is opt-in because it requires a running execution kernel with a configured tool-capable model and a pre-provisioned Collector environment. Before enabling the test, persist all nine fixed read-only MCP Tool Policies and provide healthy inputs for all ten baseline checks (Kubernetes access plus the six registered target ids). The Compose profile alone supplies transport, not Kubernetes credentials or registered probe backends:

```bash
PATROL_E2E=1 npm test -- patrol.spec.ts
```

It uses the real UI, formal Run/Activity runtime, MCP transport, Collector, server assertion engine, evidence download, kill switch, and 390px overflow check. It never inserts fixture results through an HTTP shortcut. CI enables it with the protected `PATROL_E2E_ENABLED=1` repository variable only in an environment that supplies a model.

The test enables/disables the existing MCP record and product flag, but deliberately does not create security policies or fake probe results. Follow [Ops Patrol operations](../docs/operations/ops-patrol.md#register-the-mcp-server) when preparing the protected E2E environment.

Headed mode (debugging):

```bash
npm run test:headed
```

## Related documentation

- [Governed Web Operator tutorial](../docs/tutorials/04-governed-web-operator.md)
- [Web Operator architecture](../docs/architecture/web-operator.md)
- [OpsConsole demo README](../demo/ops-console/README.md)
- [Ops Patrol tutorial](../docs/tutorials/06-ops-patrol.md)
- [Ops Patrol operations](../docs/operations/ops-patrol.md)
