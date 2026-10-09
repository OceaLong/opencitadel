"""Request-local evaluation readers and bounded durable object intent transport."""

from app.application.evaluation.dataset_service import DatasetService
from app.composition.execution_content import (
    build_execution_content_service,
    build_execution_event_service,
    build_execution_view_service,
)
from app.infrastructure.repositories.db_evaluation_dataset_repository import DatasetObjectLifecycle


def build_rule_scoring_service(*, settings, resources, shared):
    """E12 invokes this bounded consumer; all body readers use original user claims."""
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.application.services.artifact_service import ArtifactService
    from app.application.services.execution_content_service import ExecutionContentService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    def content(authorization):
        def factory():
            return shared.uow_factory(authorization_context=authorization)

        return ExecutionContentService(
            factory,
            None,
            ArtifactService(factory, shared.object_storage),
            cursor_secret=(settings.public_cursor_secret or settings.api_key_secret).encode(),
        )

    return RuleScoringService(
        shared.uow_factory,
        build_suite_service(
            settings=settings,
            resources=resources,
            shared=shared,
            authorization=AuthorizationContext.system("execution-kernel"),
        ),
        RuleEvidenceReader(shared.uow_factory, content_factory=content),
    )


def build_dataset_service(*, settings, resources, shared, authorization):
    return DatasetService(
        shared.uow_factory,
        shared.object_storage,
        DatasetObjectLifecycle(
            resources.postgres.upload_intent_session_factory,
            shared.object_storage,
            signing_secret=settings.database_authorization_signing_secret,
        ),
        cursor_secret=(settings.public_cursor_secret or settings.api_key_secret).encode(),
        content=build_execution_content_service(
            settings=settings,
            resources=resources,
            uow_factory=shared.uow_factory,
            artifacts=shared.artifact_service,
            files=shared.file_service,
            authorization=authorization,
        ),
        views=build_execution_view_service(
            settings=settings, resources=resources, authorization=authorization
        ),
        events=build_execution_event_service(
            settings=settings, resources=resources, authorization=authorization
        ),
    )


def build_suite_service(*, settings, resources, shared, authorization):
    from app.application.evaluation.environment_authority import EvaluationExternalContracts
    from app.application.evaluation.suite_service import SuiteService
    from app.domain.evaluation.configuration import DeploymentLimits

    return SuiteService(
        shared.uow_factory,
        build_dataset_service(
            settings=settings, resources=resources, shared=shared, authorization=authorization
        ),
        policies=shared.runtime_policy_repository,
        budgets=build_budget_authority(settings),
        external_contracts_factory=lambda uow: EvaluationExternalContracts(
            uow,
            build_environment_registry(settings),
            ceiling=settings.evaluation_environment_concurrency,
        ),
        cursor_secret=(settings.public_cursor_secret or settings.api_key_secret).encode(),
        limits=lambda: DeploymentLimits(
            **{key: getattr(settings, "evaluation_" + key) for key in DeploymentLimits.model_fields}
        ),
    )


def build_recording_service(*, settings, resources, shared, authorization):
    from app.application.evaluation.recording_service import RecordingService
    from app.application.evaluation.recording_source import RecordingSource

    return RecordingService(
        shared.uow_factory,
        cursor_secret=(settings.public_cursor_secret or settings.api_key_secret).encode(),
        source=RecordingSource(
            build_execution_content_service(
                settings=settings,
                resources=resources,
                uow_factory=shared.uow_factory,
                artifacts=shared.artifact_service,
                files=shared.file_service,
                authorization=authorization,
            ),
            build_execution_view_service(
                settings=settings, resources=resources, authorization=authorization
            ),
        ),
    )


def build_replay_runtime(*, settings, resources, shared):
    from app.application.evaluation.recording_authority import RecordingAuthority
    from app.application.evaluation.replay_runtime import ReplayRuntime

    return ReplayRuntime(
        RecordingAuthority(shared.uow_factory),
        shared.object_storage,
        lambda authorization: (
            build_recording_service(
                settings=settings, resources=resources, shared=shared, authorization=authorization
            ).source
        ),
    )


def build_environment_registry(settings, *, request_observer=None):
    from app.infrastructure.evaluation.environment_inventory import (
        build_environment_registry as build,
    )

    return build(settings, broker=True, request_observer=request_observer)


def build_environment_service(*, settings, resources, shared, authorization):
    from app.application.evaluation.environment_service import EnvironmentService
    from app.composition.environment_capacity import configured_environment_capacity

    return EnvironmentService(
        shared.uow_factory,
        build_environment_registry(settings),
        ceiling=settings.evaluation_environment_concurrency,
        capacity_policy=configured_environment_capacity(settings),
        cursor_secret=(settings.public_cursor_secret or settings.api_key_secret).encode(),
    )


def build_environment_runtime(*, settings, shared, request_observer=None):
    from app.application.evaluation.environment_runtime import EnvironmentRuntime

    return EnvironmentRuntime(
        shared.uow_factory,
        build_environment_registry(settings, request_observer=request_observer),
        shared.sandbox_factory,
        ceiling=settings.evaluation_environment_concurrency,
    )


def build_budget_authority(settings):
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.infrastructure.evaluation.budget_inventory import load_budget_inventory

    inventory = load_budget_inventory(
        getattr(settings, "evaluation_budget_inventory_path", ""),
        allow_acceptance=getattr(settings, "evaluation_acceptance_enabled", False)
        and settings.env == "test",
    )
    return BudgetAuthority(inventory) if inventory is not None else None


def build_batch_scheduler(*, settings, resources, shared):
    """Build the durable scheduler port; E12 owns its supervised worker lifecycle."""
    from app.application.evaluation.environment_authority import EnvironmentPreflightAuthority
    from app.application.evaluation.preflight import PreflightService
    from app.application.evaluation.recording_authority import RecordingPreflightAuthority
    from app.application.evaluation.scheduler import Scheduler
    from app.composition.evaluation_execution import configured_execution_policy
    from app.domain.models.authorization import AuthorizationContext

    authorization = AuthorizationContext.system("execution-kernel")
    suites = build_suite_service(
        settings=settings, resources=resources, shared=shared, authorization=authorization
    )
    environments = build_environment_service(
        settings=settings, resources=resources, shared=shared, authorization=authorization
    )
    return Scheduler(
        shared.uow_factory,
        suites,
        shared.run_admission_service,
        execution_policy=configured_execution_policy(settings),
        environments=environments,
        preflight_factory=lambda principal: PreflightService(
            suites,
            principal,
            recordings=RecordingPreflightAuthority(),
            environments=EnvironmentPreflightAuthority(
                environments.registry, ceiling=environments.ceiling
            ),
        ),
    )


def build_judge_service(*, settings, resources, shared):
    """E12 supervises score_batch and reconcile_batch independently of subject Runs."""
    from app.application.evaluation.judge_service import JudgeService
    from app.composition.evaluation_execution import configured_execution_policy

    rules = build_rule_scoring_service(settings=settings, resources=resources, shared=shared)
    return JudgeService(
        shared.uow_factory,
        rules.suites,
        rules.evidence,
        shared.run_admission_service,
        execution_policy=configured_execution_policy(settings),
    )


def build_review_command_consumer(*, settings, resources, shared):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer

    return ReviewCommandConsumer(
        shared.uow_factory,
        build_judge_service(settings=settings, resources=resources, shared=shared),
    )
