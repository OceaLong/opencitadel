"""One caller UoW joins C1/C2 authority, F07 receipt and physical budget intent."""

from dataclasses import dataclass

from sqlalchemy import text

from app.application.evaluation.budget_candidates import FrozenBudgetCandidates
from app.application.evaluation.budget_service import BudgetDemandFactory, require_fresh_permit
from app.application.evaluation.physical_lineage import logical_model_invocation
from app.application.services.execution_usage_service import (
    configuration_snapshot,
    request_snapshot,
)
from app.domain.evaluation.budget import BudgetBucket
from app.domain.evaluation.budget_capabilities import BudgetProfile
from app.domain.evaluation.configuration import ConfigSelection
from app.domain.execution.run import RunState, RunStatus
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.execution_usage import PriceSnapshot
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
    DBEvaluationBudgetPolicyRepository,
)
from app.infrastructure.repositories.db_evaluation_execution_repository import (
    DBEvaluationExecutionRepository,
)
from app.infrastructure.repositories.db_execution_usage_repository import DBExecutionUsageRepository


@dataclass(frozen=True)
class CommittedDispatchPermit:
    call_identity: str
    _permit: object

    def consume(self):
        self._permit.consume()
        return self.call_identity


class DurableBudgetDispatchService:
    def __init__(self, *, uow_factory, inventory, physical_policy, execution_policy):
        self.uow_factory = uow_factory
        self.inventory = inventory
        self.physical_policy = physical_policy
        self.execution_policy = execution_policy
        self.authorization = AuthorizationContext.system("execution-kernel")

    async def candidates(self, scope, context):
        async with self.uow_factory(self.authorization) as work:
            binding = await work.evaluation_budget_control.binding(scope, context.run.run_id)
            if binding is None:
                if context.run.source_entity_type in {
                    "evaluation_recorded_case",
                    "evaluation_isolated_case",
                    "evaluation_judge",
                }:
                    raise ValueError("budget_run_binding_unavailable")
                return None
            return FrozenBudgetCandidates(self.inventory, binding.candidate_proof)

    async def before_send(self, scope, request, context, model, payload, *, resolved=None):
        async with self.uow_factory(self.authorization) as work:
            identity, result = await self.reserve_in_uow(
                work, scope, request, context, model, payload, resolved=resolved
            )
            await work.commit()
        # A pending/failed commit never exposes a transport permit.
        return CommittedDispatchPermit(identity, require_fresh_permit(result))

    async def reserve_in_uow(self, work, scope, request, context, model, payload, *, resolved=None):
        repo = DBEvaluationExecutionRepository(work.db_session)
        binding = await repo.controls.binding(scope, context.run.run_id)
        if binding is None:
            from app.infrastructure.execution.ordinary_budget_dispatch import reserve_ordinary

            return await reserve_ordinary(
                work,
                scope,
                request,
                context,
                model,
                payload,
                resolved=resolved or {},
                inventory=self.inventory,
                expected_policy=self.physical_policy,
            )
        scope = (
            OwnerScope.team(binding.requester["user_id"], scope.team_id) if scope.team_id else scope
        )
        binding, namespace, execution_policy, _, lease = await repo.lock(
            scope, context.run.run_id, self.execution_policy, require_policy=False
        )
        await repo.authorize(scope, binding, namespace)
        if (
            execution_policy != self.execution_policy
            or lease is None
            or lease["phase"] != "held"
            or lease["state"] is None
        ):
            raise ValueError("budget_execution_admission_unavailable")
        state = RunState.model_validate(lease["state"])
        if (
            state.status != RunStatus.RUNNING
            or state.source_entity_type != binding.source_entity_type
            or state.source_entity_id != binding.source_entity_id
            or state.policy_snapshot.snapshot_digest != binding.policy_digest
            or context.run.policy_snapshot.snapshot_digest != binding.policy_digest
            or request.activity_id not in state.active_activity_ids
            or (request.activity_id, request.generation, context.claim_generation)
            not in state.started_activity_claims
        ):
            raise ValueError("budget_activity_admission_unavailable")
        lineage = await work.evaluation_lineage.assert_current(scope, binding.run_id)
        if lineage is None:
            raise ValueError("budget_logical_lineage_unavailable")
        judge = None
        if binding.purpose == "evaluation_judge":
            judge = await work.evaluation_judge.authorize_run(
                scope, binding.run_id, state=state, request=request
            )
            if payload.get("tools") or payload.get("functions"):
                raise ValueError("judge_tools_forbidden")
        logical_id = logical_model_invocation(
            state,
            request.activity_id,
            request.generation,
            root_run_id=lineage["root_run_id"],
            judge_protocol=judge["protocol"] if judge else None,
        )
        policy = await DBEvaluationBudgetPolicyRepository(work.db_session).active(lock=True)
        if policy != self.physical_policy or not policy.hard_evaluation_available:
            raise ValueError("budget_physical_policy_unavailable")
        candidates = FrozenBudgetCandidates(self.inventory, binding.candidate_proof)
        for candidate in candidates.proof["candidates"]:
            metadata = await work.evaluation_configuration.metadata(
                scope, ConfigSelection(model_id=candidate["identity"]["model_id"])
            )
            if (
                metadata["identity"] != candidate["identity"]
                or metadata["settings"] != candidate["base_settings"]
                or metadata["capabilities"] != candidate["capabilities"]
                or metadata["extra_params_present"]
                or not metadata["credential_configured"]
                or (
                    "price" in candidate
                    and candidate["price"]
                    != {key: metadata["price"].get(key) for key in ("input", "output")}
                )
            ):
                raise ValueError("budget_candidate_changed")
        candidate = next(
            (
                item
                for item in candidates.proof["candidates"]
                if item["identity"]["model_id"] == model.id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("budget_candidate_changed")
        candidates.validate(model, candidate, effective=True)
        profile = BudgetProfile.model_validate(candidate["profile"])
        bound = profile.bound(payload, output=candidate["output"])
        principal = Principal.model_validate(binding.requester)
        demand = BudgetDemandFactory(
            policy,
            provider_pools={key: value.pool for key, value in self.inventory.endpoints.items()},
        ).physical(
            AuthorizationContext.for_principal(principal, scope=scope),
            endpoint_id=model.endpoint.id,
            provider_kind=model.provider,
            tokens=bound.tokens,
            money=bound.money,
        )
        demand = demand.model_copy(
            update={
                "purpose": binding.purpose,
                "batch_id": str(judge["batch_id"] if judge else namespace.id),
                "buckets": (
                    *demand.buckets,
                    BudgetBucket(
                        key="5:batch:" + str(namespace.id),
                        tokens=namespace.token_budget,
                        money=namespace.money_budget,
                    ),
                    BudgetBucket(
                        key="6:purpose:"
                        + str(judge["batch_id"] if judge else namespace.id)
                        + ":"
                        + binding.purpose
                    ),
                ),
            }
        )
        # Lock physical pools before the F07 activity/claim and allocation locks.
        await work.evaluation_budget.lock_capacity(str(request.activity_id), demand)
        await work.db_session.execute(
            text(
                "INSERT INTO evaluation_model_logical_calls(id,run_id,scope_key) VALUES(:id,:run,:scope) ON CONFLICT DO NOTHING"
            ),
            {"id": logical_id, "run": lineage["root_run_id"], "scope": demand.scope},
        )
        sends = await work.db_session.scalar(
            text("SELECT sends FROM evaluation_model_logical_calls WHERE id=:id FOR UPDATE"),
            {"id": logical_id},
        )
        if sends >= 3:
            raise ValueError("budget_logical_attempts_exhausted")
        usage = DBExecutionUsageRepository(work.db_session)
        snapshot_inputs = dict(resolved or {})
        snapshot_inputs["policy_revision"] = state.policy_snapshot.execution_revision_id
        snapshot_inputs.setdefault("tool_fingerprint", None)
        snapshot_inputs["price_snapshot"] = profile.price or PriceSnapshot()
        body = configuration_snapshot(model, **snapshot_inputs)
        body["requested_model"] = model.model_name
        body["requested_model_matches_configured"] = True
        body["version_unpinned"] = False
        body["budget_inventory"] = self.inventory.fingerprint
        body["budget_logical_invocation"] = str(logical_id)
        body["evaluation_configuration_version_id"] = str(binding.config_version_id)
        config = await usage.snapshot(scope, binding.run_id, body, binding.purpose)
        identity = await usage.allocate(
            scope,
            run_id=binding.run_id,
            activity_id=request.activity_id,
            generation=request.generation,
            claim_generation=context.claim_generation,
            configuration_id=config,
            request_snapshot=request_snapshot(payload),
        )
        result = await work.evaluation_budget.reserve(identity, demand)
        await work.db_session.execute(
            text("UPDATE evaluation_model_logical_calls SET sends=sends+1 WHERE id=:id"),
            {"id": logical_id},
        )
        return identity, result

    async def _evidence(self, work, scope, identity):
        from app.domain.evaluation.budget import BudgetDemand
        from app.infrastructure.repositories.db_resource_pin_repository import scope_params

        evidence = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT r.demand,d.configuration_id,c.body FROM evaluation_budget_reservations r JOIN execution_model_dispatches d ON d.call_identity=CAST(r.call_identity AS text) AND d.scope_key=r.scope_key JOIN execution_configurations c ON c.id=d.configuration_id AND c.scope_key=d.scope_key WHERE r.scope_key=:scope AND r.call_identity=:id"
                    ),
                    {**scope_params(scope), "id": identity},
                )
            )
            .mappings()
            .one_or_none()
        )
        if evidence is None:
            raise ValueError("budget_dispatch_unavailable")
        return BudgetDemand.model_validate(evidence["demand"]), evidence

    async def mark_unknown(self, scope, identity):
        async with self.uow_factory(self.authorization) as work:
            demand, _ = await self._evidence(work, scope, identity)
            result = await work.evaluation_budget.mark_unknown(identity, demand)
            await work.commit()
            return result

    async def after_completion(self, scope, identity, revision):
        from app.infrastructure.external.llm.base_llm import normalize_usage

        return await self.after_send(scope, identity, normalize_usage(None), revision)

    async def after_send(self, scope, identity, usage, revision):
        from app.domain.evaluation.budget import BudgetSettlement
        from app.domain.models.execution_usage import content_revision

        async with self.uow_factory(self.authorization) as work:
            demand, evidence = await self._evidence(work, scope, identity)
            price = PriceSnapshot.model_validate(evidence["body"]["price"])
            cost = price.cost(usage)
            fact = {
                "call_identity": identity,
                "usage": usage,
                "model_revision": revision,
                "version_unpinned": revision != evidence["body"]["configured_model"],
                "price_revision": price.revision,
                "configuration_id": evidence["configuration_id"],
                "cost_usd": str(cost) if cost is not None else None,
            }
            counts = [
                usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            ]
            tokens = (
                counts[2]
                if (
                    all(type(value) is int and value >= 0 for value in counts)
                    and counts[0] + counts[1] == counts[2]
                    and usage.get("total_consistent") is not False
                )
                else None
            )
            # Late original evidence is authorized as kernel settlement, not a
            # new user action. Unknown categories retain their original holds.
            await work.evaluation_budget.settle(
                identity,
                demand,
                BudgetSettlement(tokens=tokens, money=cost, evidence=content_revision(fact)),
            )
            await DBExecutionUsageRepository(work.db_session).record(scope, identity, fact)
            await work.commit()
            return fact
