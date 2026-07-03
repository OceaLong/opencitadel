[English](CONTRIBUTING.md) · [简体中文](CONTRIBUTING.zh-CN.md)

# Contributing to OpenCitadel

Thank you for your interest in contributing!

## Development Setup

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm (for UI; matches CI)

### API / Worker

```bash
cd api
uv sync
uv run pytest
```

### UI

```bash
cd ui
npm install
npm run test
npm run build
```

### Full stack (local)

```bash
cp .env.example .env
# Edit .env: set LLM API keys and admin password
docker compose --profile local up --build
```

## Pull Request Process

1. Fork the repository and create a feature branch from `main`.
2. Keep changes focused; one logical change per PR.
3. Add or update tests for API changes (`api/tests/`).
4. Run `uv run pytest` and `npm run test` before submitting.
5. Update documentation if behavior or configuration changes — see [Documentation maintenance checklist](../docs/MAINTENANCE_CHECKLIST.md) and run `./scripts/check-docs.sh`.
6. Write clear commit messages (Conventional Commits preferred).

## Code Style

- **Python**: follow existing patterns; type hints encouraged; run formatters if configured in your editor.
- **TypeScript/React**: follow existing component structure; prefer server/client component boundaries already used in `ui/`.

## Good First Issues

Look for issues labeled `good first issue` — documentation, tests, and MCP server templates are great entry points.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
