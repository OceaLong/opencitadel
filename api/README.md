# API and kernel v2

[简体中文](README.zh-CN.md)

The backend has three roles: the FastAPI command/query API, the asynchronous
execution kernel, and the one-shot Alembic migrator. PostgreSQL is authoritative;
Redis is optional wake-up/cache infrastructure and never stores accepted work.

Source boundaries:

```text
app/contexts/identity/    users, teams, invitations, quotas, audit, notifications
app/contexts/inference/   endpoints, models, bindings, usage, MCP and tool adapters
app/contexts/knowledge/   files, artifacts, knowledge versions and retrieval
app/kernel/domain/        pure commands, events, reducers and workflows
app/kernel/application/   command, effect, timer, retention and rebuild services
app/kernel/infrastructure/postgres/  journal, claims and projections
app/composition/          explicit API and kernel object graphs
app/interfaces/           retained HTTP contract
```

The greenfield database has exactly one Alembic base/head,
`0001greenfield`. Runtime roles use signed transaction-local authorization and
forced RLS. Events, audit records, and governance revisions are immutable;
physical purge is a distinct signed system operation.

```bash
uv sync --all-groups
uv run alembic upgrade head
uv run ruff check --config ../ruff.toml app core tests
uv run lint-imports
uv run pytest -q
```

Entrypoints are `./run.sh`, `./execution-kernel.sh`, and `./migrate.sh`.
