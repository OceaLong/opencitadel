.PHONY: quickstart build quality-check test test-api test-ui test-patrol test-patrol-fixtures test-actuator acceptance-e2e

quickstart:
	@bash scripts/quickstart.sh

build:
	docker compose build opencitadel-sandbox opencitadel-api opencitadel-execution-kernel opencitadel-ui

quality-check:
	cd api && uv run ruff check --config ../ruff.toml . ../ops-actuator ../ops-collector ../sandbox ../scripts
	cd api && uv run ruff format --config ../ruff.toml --check . ../ops-actuator ../ops-collector ../sandbox ../scripts
	cd api && uv run lint-imports
	cd api && uv run pytest -q \
		tests/app/contracts/test_architecture_debt.py \
		tests/app/contracts/test_quality_baseline.py \
		tests/app/contracts/test_explicit_composition_boundaries.py \
		tests/app/contracts/test_runtime_deployment_contract.py \
		tests/app/test_app_factory_process.py \
		tests/app/test_execution_kernel_process.py
	cd ui && npm run format:check
	cd ui && npm run i18n:check
	cd ui && npm run api:check
	cd ui && npm run typecheck
	cd ui && npm run lint

test-api:
	cd api && uv run pytest -q

test-ui:
	cd ui && npm run test

test-patrol:
	cd ops-collector && uv run pytest -q
	cd api && uv run pytest -q tests/app/domain/models/test_patrol_pack.py tests/app/domain/services/test_patrol_assertion_engine.py tests/app/application/services/test_patrol_pack_service.py tests/app/application/services/test_patrol_run_service.py

test-patrol-fixtures:
	./scripts/run-patrol-fixtures.sh

test-actuator:
	cd ops-actuator && uv run pytest -q
	cd api && uv run pytest -q \
		tests/app/alembic/test_greenfield_schema.py \
		tests/app/application/execution/test_activity_worker.py \
		tests/app/application/execution/test_family_decisions.py \
		tests/app/application/services/test_patrol_remediation_service.py \
		tests/app/contracts/test_greenfield_execution_boundaries.py \
		tests/app/integration/test_patrol_remediation_rbac.py

acceptance-e2e:
	./scripts/run-acceptance-e2e.sh

test: test-api test-ui
