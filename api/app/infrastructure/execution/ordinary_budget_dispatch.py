"""Ordinary Run sends retain F07 semantics while sharing durable occupancy."""

from sqlalchemy import text

from app.application.evaluation.budget_service import BudgetDemandFactory
from app.application.services.execution_usage_service import (
    configuration_snapshot,
    request_snapshot,
)
from app.domain.evaluation.budget import BudgetBucket, BudgetDemand
from app.domain.execution.aggregate import replay
from app.domain.execution.run import RunAggregate, RunStatus
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.execution_usage import PriceSnapshot
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository,
)
from app.infrastructure.repositories.db_physical_requester_repository import (
    DBPhysicalRequesterRepository,
)
from app.infrastructure.repositories.db_resource_pin_repository import scope_params


async def reserve_ordinary(
    work, scope, request, context, model, payload, *, resolved, inventory, expected_policy
):
    p = {**scope_params(scope), "run": context.run.run_id}
    admitted = (
        (
            await work.db_session.execute(
                text(
                    "SELECT id,body,purpose FROM execution_configurations WHERE scope_key=:scope AND run_id=:run AND body->>'stage'='admission' ORDER BY created_at,id LIMIT 1"
                ),
                p,
            )
        )
        .mappings()
        .first()
    )
    policy = await work.evaluation_physical_policy.active(lock=True)
    if policy is None or policy != expected_policy:
        raise ValueError("budget_policy_changed")
    requester = DBPhysicalRequesterRepository(
        work.db_session, signing_secret=work.evaluation_budget.secret
    )
    proof = await requester.resolve(admitted, scope=scope, run_id=context.run.run_id)
    pools = {key: value.pool for key, value in inventory.endpoints.items()} if inventory else {}
    factory = BudgetDemandFactory(policy, provider_pools=pools)
    if proof["kind"] in {"user", "legacy_personal_owner"}:
        principal = Principal.model_validate(proof["principal"])
        scope = OwnerScope.team(principal.user_id, scope.team_id) if scope.team_id else scope
        await DBEvaluationDatasetRepository(work.db_session).authorize(scope, principal, write=True)
        demand = factory.physical(
            AuthorizationContext.for_principal(principal, scope=scope),
            endpoint_id=model.endpoint.id,
            provider_kind=model.provider,
            tokens=None,
            money=None,
        )
    elif proof["kind"] == "legacy_unknown_requester" and scope.team_id:
        demand = BudgetDemand(
            scope=scope_params(scope)["scope"],
            requester="legacy_unknown_requester",
            purpose="unknown",
            policy_revision=policy.revision,
            buckets=(
                BudgetBucket(key="0:global", slots=policy.global_concurrency),
                *factory.provider_buckets(
                    endpoint_id=model.endpoint.id, provider_kind=model.provider
                ),
                BudgetBucket(key="2:user:legacy_unknown_requester", slots=policy.user_concurrency),
            ),
        )
    elif proof["kind"] == "system" and isinstance(proof.get("actor"), str) and proof["actor"]:
        import hashlib

        actor = "system:sha256:" + hashlib.sha256(proof["actor"].encode()).hexdigest()
        demand = BudgetDemand(
            scope=scope_params(scope)["scope"],
            requester=actor if scope.team_id else scope.user_id,
            purpose="unknown",
            policy_revision=policy.revision,
            buckets=(
                BudgetBucket(key="0:global", slots=policy.global_concurrency),
                *factory.provider_buckets(
                    endpoint_id=model.endpoint.id, provider_kind=model.provider
                ),
                BudgetBucket(key="2:user:" + actor, slots=policy.user_concurrency),
                *(
                    (BudgetBucket(key="2:user:" + scope.user_id, slots=policy.user_concurrency),)
                    if not scope.team_id
                    else ()
                ),
            ),
        )
    else:
        raise ValueError("physical_requester_proof_invalid")
    purpose = admitted["purpose"] if admitted else "unknown"
    if purpose not in {"production", "unknown"}:
        raise ValueError("budget_run_binding_unavailable")
    demand = demand.model_copy(update={"purpose": purpose})
    await work.evaluation_budget.lock_capacity(str(request.activity_id), demand)
    key = PostgresEventStore._scope_advisory_lock_key(
        None if scope.team_id else scope.user_id, scope.team_id
    )
    await work.db_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    aggregate = RunAggregate()
    events = await PostgresEventStore(
        work.db_session, event_registries={"run": aggregate.event_registry}
    ).load_stream("run", str(context.run.run_id))
    state = replay(aggregate, events, stream_id=str(context.run.run_id)).state
    if (
        state.status != RunStatus.RUNNING
        or state.source_entity_type
        in {"evaluation_recorded_case", "evaluation_isolated_case", "evaluation_judge"}
        or state.policy_snapshot != context.run.policy_snapshot
        or (request.activity_id, request.generation, context.claim_generation)
        not in state.started_activity_claims
        or request.activity_id not in state.active_activity_ids
    ):
        raise ValueError("budget_activity_admission_unavailable")
    body = configuration_snapshot(model, **resolved)
    requested = payload.get("model", model.model_name)
    matches = requested == model.model_name
    body["requested_model"] = requested
    body["requested_model_matches_configured"] = matches
    if not matches:
        price = PriceSnapshot()
    elif admitted and all(
        admitted["body"].get(key) == value
        for key, value in (
            ("model_id", model.id),
            ("configured_model", requested),
            ("provider", model.provider.value),
            ("endpoint_id", model.endpoint.id),
        )
    ):
        price = PriceSnapshot.model_validate(admitted["body"]["price"])
    else:
        price = PriceSnapshot.model_validate(body["price"])
    body["price"], body["price_revision"] = price.model_dump(mode="json"), price.revision
    body["physical_requester_provenance"] = proof["kind"]
    repository = work.execution_usage
    config_id = await repository.snapshot(scope, context.run.run_id, body, purpose)
    identity = await repository.allocate(
        scope,
        run_id=context.run.run_id,
        activity_id=request.activity_id,
        activity_type=request.activity_type,
        generation=request.generation,
        claim_generation=context.claim_generation,
        configuration_id=config_id,
        request_snapshot=request_snapshot(payload),
    )
    return identity, await work.evaluation_budget.reserve(identity, demand)
