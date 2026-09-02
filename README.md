# OpenCitadel v2

[简体中文](README.zh-CN.md)

OpenCitadel is a self-hosted agent runtime built around one durable execution
kernel. The v2 cutover is intentionally incompatible with the previous
product: it starts from an empty PostgreSQL database and retains only Agent
Runs, approvals, knowledge, inference configuration, MCP tools, teams,
governance, quotas, audit, and notifications.

## Why the kernel is different

- PostgreSQL commands and append-only events are the only workflow authority.
- Pure reducers derive state; projections can be deleted and rebuilt.
- External work is one of five durable effects: `model.call`,
  `knowledge.retrieve`, `tool.call`, `file.operation`, or `knowledge.build`.
- Every effect has an idempotency key, timeout, bounded retry policy, and
  persisted outcome.
- Approval freezes the reviewer set and converges on approve, reject, expire,
  cancel, or error.
- Docker and Kubernetes create one isolated, resource-bounded sandbox per Run.
- Signed PostgreSQL authorization context plus forced RLS protects every
  tenant-owned table.

## Core product

| Area | UI | API root |
| --- | --- | --- |
| Agent Runs | `/`, `/runs/[id]` | `/api/runs` |
| Approval inbox | `/approvals` | `/api/approvals` |
| Files and knowledge | `/knowledge` | `/api/files`, `/api/knowledge-bases` |
| Inference and MCP | `/settings` | `/api/inference`, `/api/integrations/mcp` |
| Teams | `/teams` | `/api/teams`, `/api/invitations` |
| Administration | `/admin` | `/api/admin`, `/api/governance-policy` |

## Quick start

```bash
make quickstart
```

The script creates `.env` with independent local secrets, builds the sandbox
image, runs the one-way greenfield migration, and starts the stack at
`http://localhost:8088`. To intentionally erase local application data first:

```bash
bash scripts/quickstart.sh --reset-data
```

After login, configure an OpenAI-compatible endpoint, model, and binding under
Settings, then start a Run. MinIO is enabled for local file storage.

## Development

```bash
cd api && uv sync --all-groups && uv run pytest -q
cd ui && npm install && npm run typecheck && npm test
cd sandbox && uv sync && uv run pytest -q
```

See [kernel architecture](docs/architecture/kernel-v2.md),
[deployment](docs/operations/deployment.md), [API](api/README.md), and
[UI](ui/README.md).

Licensed under [Apache 2.0](LICENSE).
