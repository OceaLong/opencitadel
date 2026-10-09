# OpsConsole — Demo Internal Ticket Backend

[简体中文](README.zh-CN.md)

Form-first internal ticket and settlement console for **OpenCitadel Web Operator** demos and e2e tests. Day-to-day operations are browser-driven; a **read-only REST API** is available for reconciliation skills.

## Features

- Cookie-based login (`agent` / `agent123`)
- Ticket list with status/assignee filters
- Settlement ledger and reconciliation expected-values views
- Normal operations: update status, assign, add notes (form POST only)
- High-risk operations (separate confirmation pages):
  - **Close ticket** — type `close` to confirm
  - **Process refund** — type `refund` to confirm

## Read-only REST API

| Method | Path                           | Description                    |
| ------ | ------------------------------ | ------------------------------ |
| GET    | `/api/tickets`                 | Ticket list (JSON)             |
| GET    | `/api/settlements`             | Settlement ledger              |
| GET    | `/api/reconciliation/expected` | Expected reconciliation values |
| GET    | `/health`                      | Health check                   |

Business writes use authenticated HTML form POSTs. The extra `POST /api/_seed/reset` write endpoint recreates SQLite demo data; it and the JSON read endpoints require no login and have no environment gate. Use this service only for trusted local demos/tests with demo data.

## Run locally

```bash
cd demo/ops-console
pip install -r requirements.txt
python seed.py
uvicorn app:app --host 0.0.0.0 --port 9099
```

Open http://localhost:9099

## Docker (with OpenCitadel)

```bash
docker compose --profile local --profile demo up -d --build ops-console
```

Service hostname inside Docker network: `ops-console:9099`

Compose joins the application and sandbox networks and publishes host `${OPS_CONSOLE_PORT:-9099}` to container 9099. Sessions live in process memory and require login again after restart. SQLite data lives in the container working directory; the shipped Compose service has no persistent volume.

## Stable element IDs

Pages use stable `id` attributes for browser automation (`#login-form`, `#btn-confirm-close`, `#btn-confirm-refund`, etc.).
