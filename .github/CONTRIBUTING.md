[English](CONTRIBUTING.md) · [简体中文](CONTRIBUTING.zh-CN.md)

# Contributing to OpenCitadel

Thank you for your interest in contributing!

## Development Setup

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm (for UI; matches CI)

### API / Execution Kernel

```bash
cd api
uv sync --all-groups
uv run pytest --ignore=tests/app/integration/test_execution_visualization_closed_loop.py
```

### UI

```bash
cd ui
npm ci
npm run test
npm run build
```

### Quality gates

From the repository root, run the same read-only static checks as CI:

```bash
make quality-check
./scripts/check-docs.sh
```

`make quality-check` runs repository-wide Ruff lint/format checks, Python
architecture/process contracts, then UI Prettier, generated API contract,
strict i18n, TypeScript, and ESLint.
The authoritative translation sources are `ui/messages/en.json` and
`ui/messages/zh.json`; update both directly. There is no catalog generation
step.

PostgreSQL/Redis integration suites require fresh schema roles and real services; `make test-api-strict` requires those dependencies. Ordinary runs can skip unconfigured integration cases. See [E2E acceptance](../e2e/README.md) for the isolated stack and capacity gate.
The API job and both Make targets exclude only the six current-invocation consumers in `test_execution_visualization_closed_loop.py`; the acceptance runner executes them with zero skips after validating their native strict report and restoration receipt.

### Full stack (local)

```bash
cp .env.example .env
# Edit .env: set the admin password; add inference credentials after login
docker compose --profile local up --build
```

## Pull Request Process

1. Fork the repository and create a feature branch from `main`.
2. Keep changes focused; one logical change per PR.
3. Add or update tests for API changes (`api/tests/`).
4. Run `make quality-check`, API/UI tests, and the UI production build before submitting.
5. Update documentation if behavior or configuration changes — see [Documentation maintenance checklist](../docs/MAINTENANCE_CHECKLIST.md) and run `./scripts/check-docs.sh`.
6. Write clear commit messages (Conventional Commits preferred).

## Code Style

- **Python**: Ruff policy is defined once in root `ruff.toml`; no baseline or per-file debt waivers.
- **TypeScript/React**: use the existing Prettier and ESLint configuration and preserve established Server/Client boundaries.
- **UI copy**: use next-intl catalogs; strict checks reject missing, unused, unregistered dynamic, and hardcoded user-facing text.

## Good First Issues

Look for issues labeled `good first issue` — documentation, tests, and MCP server templates are great entry points.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
