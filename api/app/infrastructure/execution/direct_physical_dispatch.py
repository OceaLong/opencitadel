"""Durable direct-request occupancy without fabricating an execution Run."""

import json
from uuid import uuid4

from app.application.evaluation.budget_service import BudgetDemandFactory, require_fresh_permit
from app.domain.evaluation.budget import BudgetSettlement, DirectPhysicalRequest
from app.domain.evaluation.configuration import digest
from app.domain.models.authorization import AuthorizationMode
from app.infrastructure.execution.budget_dispatch import CommittedDispatchPermit


class DirectPhysicalDispatchService:
    def __init__(self, *, uow_factory, inventory, physical_policy):
        self.uow_factory = uow_factory
        self.policy = physical_policy
        pools = {key: value.pool for key, value in inventory.endpoints.items()} if inventory else {}
        self.demands = BudgetDemandFactory(physical_policy, provider_pools=pools)

    def guard(self, scope, authorization, *, purpose):
        if (
            authorization.mode != AuthorizationMode.USER
            or authorization.scope is None
            or scope != authorization.scope
        ):
            raise ValueError("budget_scope_authority_required")
        return DirectPhysicalDispatchGuard(self, authorization, purpose)


class DirectPhysicalDispatchGuard:
    def __init__(self, service, authorization, purpose):
        self.service = service
        self.authorization = authorization.model_copy(deep=True)
        self.purpose = purpose
        self.receipts = {}

    async def before_send(self, model, payload):
        demand = self.service.demands.physical(
            self.authorization,
            endpoint_id=model.endpoint.id,
            provider_kind=model.provider,
            tokens=None,
            money=None,
        )
        identity = str(uuid4())
        demand = demand.model_copy(
            update={
                "direct_request": DirectPhysicalRequest(
                    request_id=identity,
                    purpose=self.purpose,
                    endpoint_id=model.endpoint.id,
                    provider=model.provider.value,
                    configured_model=model.model_name,
                    wire_model=payload.get("model", model.model_name),
                    payload_digest=digest(payload),
                )
            }
        )
        async with self.service.uow_factory(self.authorization) as work:
            result = await work.evaluation_budget.reserve(identity, demand)
            await work.commit()
        self.receipts[identity] = demand
        return CommittedDispatchPermit(identity, require_fresh_permit(result))

    async def after_completion(self, identity, revision):
        await self.after_send(identity, {}, revision)

    async def after_send(self, identity, usage, revision):
        demand = self.receipts[identity]
        # No direct-call price authority: unknown amounts remain unknown. A
        # complete provider response still conclusively ends physical occupancy.
        total = usage.get("native_total_tokens")
        if total is None:
            total = usage.get("total_tokens")
        if type(total) is not int or total < 0 or usage.get("total_consistent") is False:
            total = None
        fact = BudgetSettlement(
            tokens=total,
            evidence=json.dumps({"usage": usage, "revision": revision}, sort_keys=True),
        )
        async with self.service.uow_factory(self.authorization) as work:
            await work.evaluation_budget.complete_direct(identity, demand, fact)
            await work.commit()
