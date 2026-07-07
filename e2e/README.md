[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel E2E Tests

Playwright end-to-end smoke tests for the OpenCitadel UI and the **Web Operator demo backend** (OpsConsole).

## Scope

| Suite | File | What it covers |
|-------|------|----------------|
| OpsConsole demo | `web-operator.spec.ts` | Login page, ticket list after login |
| Platform smoke | `web-operator.spec.ts` | OpenCitadel home page loads |

These tests support [Tutorial 4: Governed Web Operator](../docs/tutorials/04-governed-web-operator.md) — run them after standing up the demo stack.

**What is not covered**: settings modal, HITL gates, team invitations, knowledge-base ingest, codebase flows, admin console, or mobile navigation. UI unit tests live in `ui/src/**/*.test.ts` (logic only, no component regression). Do not treat `npm test` in `ui/` or `e2e/` as full UI coverage.

## Prerequisites

- Node.js >= 22
- Running OpenCitadel stack (default `http://localhost:8088`)
- For OpsConsole tests: demo profile enabled

```bash
# From repo root — start platform + demo OpsConsole
docker compose --profile local --profile demo up -d --build
docker compose build opencitadel-sandbox   # if not already built
```

OpsConsole default URL: `http://localhost:9099` (override with `OPS_CONSOLE_URL`).

## Install and run

```bash
cd e2e
npm install
npm test
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAYWRIGHT_BASE_URL` | `http://localhost:8088` | OpenCitadel UI base URL |
| `OPS_CONSOLE_URL` | `http://localhost:9099` | OpsConsole demo backend |

Headed mode (debugging):

```bash
npm run test:headed
```

## Related documentation

- [Governed Web Operator tutorial](../docs/tutorials/04-governed-web-operator.md)
- [Web Operator architecture](../docs/architecture/web-operator.md)
- [OpsConsole demo README](../demo/ops-console/README.md)
