"""Run existing execution compatibility assertions without shared-schema fixtures."""

# ruff: noqa: F401,F811
import importlib

import pytest

from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_batch_repository import actual_handler
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]

CASES = [
    (
        "tests.app.application.execution.test_orchestrator",
        "test_duplicate_accepted_command_returns_persisted_result_and_one_event",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_duplicate_business_rejection_is_persisted_without_events",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_unknown_command_is_rejected_with_a_stable_public_code",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_invalid_latest_command_schema_is_durably_rejected",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_oversized_command_is_stored_as_digest_only_and_durably_rejected",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_invalid_historical_event_schema_fails_closed_before_decision",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_crash_after_inbox_receive_is_recovered_by_orchestrator",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_command_in_progress_is_reported_as_deferred_and_not_fatal",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_optimistic_conflict_reloads_and_redecides",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_inbox_result_events_and_outbox_are_one_transaction",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_activity_completion_event_and_operational_task_are_one_transaction",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_corrupt_stream_fails_closed_before_decide_or_append",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_command_replay_loads_valid_snapshot_and_only_its_event_tail",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_corrupt_snapshot_is_deleted_and_command_falls_back_to_full_replay",
    ),
    (
        "tests.app.application.execution.test_orchestrator",
        "test_appended_cancellation_event_atomically_cancels_matching_timer",
    ),
    (
        "tests.app.application.execution.test_timer_dispatcher",
        "test_due_timer_inserts_one_deterministic_command_and_fires_once",
    ),
    (
        "tests.app.application.execution.test_timer_dispatcher",
        "test_cancelled_timer_never_enters_inbox",
    ),
    (
        "tests.app.application.execution.test_timer_dispatcher",
        "test_stale_timer_is_durably_rejected_without_events",
    ),
]


@pytest.mark.parametrize(("module_name", "test_name"), CASES, ids=[name for _, name in CASES])
async def test_existing_execution_contract_in_owned_database(
    budget_binding_fixture, module_name, test_name
):
    # The original fixture would call postgres_integration/_db_schema. Do not
    # request it: these already-migrated, individually owned databases replace it.
    sessions = actual_handler(
        budget_binding_fixture, ExecutionSlotPolicy(revision=1)
    )._session_factory
    await getattr(importlib.import_module(module_name), test_name)((sessions, [], []))
