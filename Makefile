.PHONY: quickstart build lint quality-check test test-api test-ui test-sandbox

PY_LINT_PATHS := app core tests

quickstart:
	@bash scripts/quickstart.sh

build:
	docker compose build opencitadel-sandbox opencitadel-api opencitadel-execution-kernel opencitadel-ui

lint:
	cd api && uv run ruff check --config ../ruff.toml $(PY_LINT_PATHS)
	cd api && uv run ruff format --config ../ruff.toml --check $(PY_LINT_PATHS)

quality-check: lint
	cd api && uv run lint-imports
	cd api && uv run pytest -q tests/app/contracts tests/app/alembic/test_greenfield_schema.py
	cd ui && npm run typecheck
	cd ui && npm run lint

test-api:
	cd api && uv run pytest -q

test-ui:
	cd ui && npm run test

test-sandbox:
	cd sandbox && uv run pytest -q

test: test-api test-ui test-sandbox
