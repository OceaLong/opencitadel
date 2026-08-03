.PHONY: quickstart build test test-api test-ui test-patrol test-patrol-fixtures

quickstart:
	@bash scripts/quickstart.sh

build:
	docker compose build opencitadel-sandbox opencitadel-api opencitadel-worker opencitadel-ui

test-api:
	cd api && uv run pytest -q

test-ui:
	cd ui && npm run test

test-patrol:
	cd ops-collector && uv run pytest -q
	cd api && uv run pytest -q tests/app/domain/models/test_patrol_pack.py tests/app/domain/services/test_patrol_assertion_engine.py tests/app/application/services/test_patrol_pack_service.py tests/app/application/services/test_patrol_run_service.py

test-patrol-fixtures:
	./scripts/run-patrol-fixtures.sh

test: test-api test-ui
