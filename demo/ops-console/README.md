# OpsConsole — Demo Internal Ticket Backend

Form-only internal ticket operations console for **OpenCitadel Web Operator** demos and e2e tests. No REST API — browser automation only.

## Features

- Cookie-based login (`agent` / `agent123`)
- Ticket list with status/assignee filters
- Normal operations: update status, assign, add notes
- High-risk operations (separate confirmation pages):
  - **Close ticket** — type `close` to confirm
  - **Process refund** — type `refund` to confirm

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
docker compose --profile local --profile demo up ops-console
```

Service hostname inside Docker network: `ops-console:9099`

## Stable element IDs

Pages use stable `id` attributes for browser automation (`#login-form`, `#btn-confirm-close`, `#btn-confirm-refund`, etc.).
