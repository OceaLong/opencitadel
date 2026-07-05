.PHONY: quickstart build test test-api test-ui

quickstart:
	@bash scripts/quickstart.sh

build:
	docker compose build opencitadel-sandbox opencitadel-api opencitadel-worker opencitadel-ui

test-api:
	cd api && uv run pytest -q

test-ui:
	cd ui && npm run test

test: test-api test-ui
