.PHONY: quickstart build test test-api test-ui

quickstart:
	@bash scripts/quickstart.sh

build:
	docker compose build

test-api:
	cd api && uv run pytest -q

test-ui:
	cd ui && npm run test

test: test-api test-ui
